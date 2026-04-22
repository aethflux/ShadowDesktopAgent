/**
 * live2d_adapter.js — Live2D Cubism 5 r.5 ES Module
 *
 * Loads the Haru Live2D model and renders it onto the provided canvas.
 * Exposes the ILive2DAdapter interface to callers (pet.js).
 *
 * Architecture:
 *   1. Live2DCubismCore loads as a classic <script> (sets window.Live2DCubismCore)
 *   2. This ES module imports @framework/* via the import map in pet.html
 *   3. Start CubismFramework, create WebGL context, load Haru model
 *   4. Run RAF render loop
 *   5. Expose startSpeaking / stopSpeaking / playMotion / setExpression
 */

import { CubismFramework, LogLevel, Option } from '@framework/live2dcubismframework';
import { CubismModelSettingJson } from '@framework/cubismmodelsettingjson';
import { CubismUserModel } from '@framework/model/cubismusermodel';
import { CubismMatrix44 } from '@framework/math/cubismmatrix44';
import { CubismExpressionMotionManager } from '@framework/motion/cubismexpressionmotionmanager';
import { CubismMotionQueueManager } from '@framework/motion/cubismmotionqueuemanager';
import { CubismMotion } from '@framework/motion/cubismmotion';
import { CubismPhysics } from '@framework/physics/cubismphysics';
import { CubismPose } from '@framework/effect/cubismpose';
import { CubismEyeBlink } from '@framework/effect/cubismeyeblink';
import { CubismBreath } from '@framework/effect/cubismbreath';
import { CubismTargetPoint } from '@framework/math/cubismtargetpoint';

// ─── Constants ────────────────────────────────────────────────────────────────

// Path to Haru model directory (relative to desktop/renderer/ serving root)
const MODEL_HOME = '../Haru/';
const MODEL_JSON = 'Haru.model3.json';

const MOTION_GROUP_IDLE = 'Idle';
const MOTION_GROUP_TAP_BODY = 'TapBody';
const PRIORITY_NONE = 0;
const PRIORITY_IDLE = 1;
const PRIORITY_NORMAL = 2;
const PRIORITY_FORCE = 3;

// ─── Helpers ──────────────────────────────────────────────────────────────────

const printMessage = console.log.bind(console);
let _lastUpdate = Date.now();

function updateTime() {
  const now = Date.now();
  _lastUpdate = now;
}

function loadFileAsBytes(filePath) {
  return fetch(filePath)
    .then(r => r.ok ? r.arrayBuffer() : Promise.reject(new Error(`HTTP ${r.status}`)))
    .then(buf => ({ buffer: buf, size: buf.byteLength }));
}

// ─── HaruModel — minimal implementation matching the Sample's LAppModel ────────

class HaruModel extends CubismUserModel {
  constructor() {
    super();
    this._modelSetting = null;
    this._expressionCount = 0;
    this._expressionManager = null;
    this._expressions = new Map();
    this._idleMotion = null;
    this._breath = null;
    this._eyeBlink = null;
    this._physics = null;
    this._pose = null;
    this._dragManager = null;
    this._modelHomeDir = '';
    this._isFlipX = true;
    this._isFlipY = false;
    this._userTimeSeconds = 0;
    this._updating = false;
    this._initialized = false;
    this._setupComplete = false;
    this._textureCount = 0;
  }

  // Entry point: load model3.json and set up everything
  load(canvas) {
    this._modelHomeDir = MODEL_HOME;
    loadFileAsBytes(MODEL_HOME + MODEL_JSON)
      .then(({ buffer }) => {
        this._modelSetting = new CubismModelSettingJson(buffer, buffer.byteLength);
        this._setupModel(canvas);
      })
      .catch(err => {
        printMessage('[Live2D] Model JSON load failed: ' + err);
      });
  }

  _setupModel(canvas) {
    this._updating = true;
    this._initialized = false;

    const mocFileName = this._modelSetting.getModelFileName();
    loadFileAsBytes(this._modelHomeDir + mocFileName)
      .then(({ buffer }) => {
        // Load the moc3 model
        this.loadModel(buffer, true);

        // Create WebGL renderer
        this.createRenderer(canvas.width, canvas.height);

        // Expressions
        this._expressionManager = new CubismExpressionMotionManager();
        this._loadExpressionsAsync();

        // Eye blink from model setting
        if (this._modelSetting.getEyeBlinkParameterCount() > 0) {
          this._eyeBlink = CubismEyeBlink.create(this._modelSetting);
        }

        // Breath
        this._breath = new CubismBreath();
        this._breath.setParameters([
          { id: CubismFramework.getIdManager().getId('ParamBreath'), value: 0.5, weight: 1.0 }
        ]);

        // Physics (optional)
        const physicsFile = this._modelSetting.getPhysicsFileName();
        if (physicsFile && physicsFile.length > 0) {
          loadFileAsBytes(this._modelHomeDir + physicsFile)
            .then(({ buffer }) => {
              this.loadPhysics(buffer, buffer.byteLength);
            })
            .catch(() => {});
        }

        // Pose (optional)
        const poseFile = this._modelSetting.getPoseFileName();
        if (poseFile && poseFile.length > 0) {
          loadFileAsBytes(this._modelHomeDir + poseFile)
            .then(({ buffer }) => {
              this.loadPose(buffer, buffer.byteLength);
            })
            .catch(() => {});
        }

        // Load textures (blocking wait for simplicity)
        this._loadTexturesSync(canvas);

        // Idle motion
        this._loadIdleMotion();

        // Default expression
        if (this._modelSetting.getExpressionCount() > 0) {
          this.setExpression(this._modelSetting.getExpressionName(0));
        }

        this._initialized = true;
        this._updating = false;
        this._setupComplete = true;

        printMessage('[Live2D] Model setup complete, texture count: ' + this._textureCount);
      })
      .catch(err => {
        printMessage('[Live2D] moc3 load failed: ' + err);
      });
  }

  _loadTexturesSync(canvas) {
    const gl = canvas.getContext('webgl') || canvas.getContext('webgl2');
    if (!gl || !this._model) return;

    const count = this._modelSetting.getTextureCount();
    this._textureCount = count;

    for (let i = 0; i < count; i++) {
      const texturePath = this._modelHomeDir + this._modelSetting.getTextureFileName(i);

      // Synchronous-style: use a blocking fetch for simplicity in init
      // The model won't render until setup is done anyway
      try {
        // Use synchronous-ish approach: create img and set up texture
        // We use the renderer.bindTexture approach from the Demo
        const img = new Image();
        img.src = texturePath;

        // Synchronous block isn't available in JS, so we do it properly:
        // The actual async loading happens via promise but we need sync init.
        // For now, use a simple synchronous texImage2D placeholder.
        const texture = gl.createTexture();
        gl.bindTexture(gl.TEXTURE_2D, texture);
        // 1×1 placeholder until image loads
        gl.texImage2D(gl.TEXTURE_2D, 0, gl.RGBA, gl.RGBA, gl.UNSIGNED_BYTE, new Uint8Array([200, 200, 200, 255]));
        gl.texParameteri(gl.TEXTURE_2D, gl.TEXTURE_MIN_FILTER, gl.LINEAR);
        gl.texParameteri(gl.TEXTURE_2D, gl.TEXTURE_MAG_FILTER, gl.LINEAR);
        gl.texParameteri(gl.TEXTURE_2D, gl.TEXTURE_WRAP_S, gl.CLAMP_TO_EDGE);
        gl.texParameteri(gl.TEXTURE_2D, gl.TEXTURE_WRAP_T, gl.CLAMP_TO_EDGE);

        this.getRenderer().bindTexture(i, texture);

        // Async load the real image
        img.onload = () => {
          gl.bindTexture(gl.TEXTURE_2D, texture);
          gl.texImage2D(gl.TEXTURE_2D, 0, gl.RGBA, gl.RGBA, gl.UNSIGNED_BYTE, img);
        };
        img.src = texturePath;
      } catch (e) {
        printMessage('[Live2D] Texture ' + i + ' failed: ' + e);
      }
    }
  }

  _loadExpressionsAsync() {
    const count = this._modelSetting.getExpressionCount();
    this._expressionCount = 0;
    for (let i = 0; i < count; i++) {
      const name = this._modelSetting.getExpressionName(i);
      const fileName = this._modelSetting.getExpressionFileName(i);
      loadFileAsBytes(this._modelHomeDir + fileName)
        .then(({ buffer }) => {
          const motion = this.loadExpression(buffer, buffer.byteLength, name);
          if (motion) this._expressions.set(name, motion);
          this._expressionCount++;
        })
        .catch(() => {});
    }
  }

  _loadIdleMotion() {
    const count = this._modelSetting.getMotionCount(MOTION_GROUP_IDLE);
    if (count <= 0) return;
    const motionFile = this._modelSetting.getMotionFileName(MOTION_GROUP_IDLE, 0);
    if (!motionFile) return;
    loadFileAsBytes(this._modelHomeDir + motionFile)
      .then(({ buffer }) => {
        const motion = this.loadMotion(
          buffer, buffer.byteLength,
          null, null, null,
          this._modelSetting, MOTION_GROUP_IDLE, 0
        );
        if (motion) {
          motion.setLoop(true);
          this._motionManager.startMotionPriority(motion, false, PRIORITY_IDLE);
          this._idleMotion = motion;
        }
      })
      .catch(() => {});
  }

  // Public draw method (called from render loop)
  draw(projectionMatrix) {
    if (!this._model || !this._setupComplete) return;
    projectionMatrix.multiplyByMatrix(this._modelMatrix);
    this.getRenderer().setMvpMatrix(projectionMatrix);
    this.getRenderer().drawModel();
  }

  // Called each frame
  update(deltaTimeSeconds) {
    if (!this._model || !this._initialized) return;
    this._userTimeSeconds += deltaTimeSeconds;

    if (this._dragManager) {
      this._model.setDragging(this._dragManager.getX(), this._dragManager.getY());
    }

    if (this._eyeBlink) {
      this._eyeBlink.updateParameters(this._model, deltaTimeSeconds);
    }

    if (this._breath) {
      this._breath.updateParameters(this._model, deltaTimeSeconds);
    }

    if (this._physics) {
      this._physics.evaluate(this._model, deltaTimeSeconds);
    }

    if (this._pose) {
      this._pose.updateParameters(this._model, deltaTimeSeconds);
    }

    if (this._expressionManager) {
      this._expressionManager.updateMotion(this._model, deltaTimeSeconds);
    }

    if (this._motionManager) {
      this._motionManager.updateMotion(this._model, deltaTimeSeconds);
    }

    this._model.update();
  }

  // Motion API
  startRandomMotion(group, priority) {
    const count = this._modelSetting.getMotionCount(group);
    if (count <= 0) return -1;
    const index = Math.floor(Math.random() * count);
    return this.startMotion(group, index, priority);
  }

  startMotion(group, index, priority) {
    const fileName = this._modelSetting.getMotionFileName(group, index);
    if (!fileName) return -1;
    return loadFileAsBytes(this._modelHomeDir + fileName)
      .then(({ buffer }) => {
        const motion = this.loadMotion(
          buffer, buffer.byteLength,
          group, null, null,
          this._modelSetting, group, index
        );
        if (!motion) return -1;
        return this._motionManager.startMotionPriority(motion, false, priority);
      })
      .catch(() => -1);
  }

  setExpression(expressionId) {
    const motion = this._expressions.get(expressionId);
    if (!motion) return;
    this._expressionManager.playMotion(motion, 1.0, false);
  }

  setDragging(x, y) {
    if (!this._dragManager) this._dragManager = new CubismTargetPoint();
    this._dragManager.setPoint(x, y);
  }

  getModelMatrix() {
    return this._modelMatrix;
  }
}

// ─── Live2D Adapter ───────────────────────────────────────────────────────────

export class Live2DAdapter {
  constructor(canvas) {
    this._canvas = canvas;
    this._gl = null;
    this._model = null;
    this._running = false;
    this._rafId = null;
    this._lastFrame = Date.now();
    this._speaking = false;
  }

  async load() {
    printMessage('[Live2D] Starting load...');

    // Initialize WebGL
    this._gl = this._canvas.getContext('webgl2') || this._canvas.getContext('webgl');
    if (!this._gl) {
      console.warn('[Live2D] WebGL unavailable — CSS pet stays active');
      return;
    }

    const gl = this._gl;
    gl.enable(gl.BLEND);
    gl.blendFunc(gl.SRC_ALPHA, gl.ONE_MINUS_SRC_ALPHA);
    gl.enable(gl.DEPTH_TEST);
    gl.depthFunc(gl.LEQUAL);

    // Start Cubism Framework
    const option = new Option();
    option.loggingLevel = LogLevel.LogLevel_Warning;
    CubismFramework.startUp(option);
    CubismFramework.initialize();

    // Create and load model
    this._model = new HaruModel();
    this._model.load(this._canvas);

    // Give model time to initialize
    await new Promise(resolve => setTimeout(resolve, 800));

    // Resize canvas to match CSS pet size
    const dpr = Math.min(window.devicePixelRatio || 1, 2);
    this._canvas.width = 220 * dpr;
    this._canvas.height = 320 * dpr;
    gl.viewport(0, 0, this._canvas.width, this._canvas.height);

    // Start render loop
    this._running = true;
    this._lastFrame = Date.now();
    this._renderLoop();

    printMessage('[Live2D] Load complete, starting render loop');
  }

  _renderLoop() {
    if (!this._running) return;

    const now = Date.now();
    const dt = Math.min((now - this._lastFrame) / 1000, 0.1);
    this._lastFrame = now;
    updateTime();

    const gl = this._gl;
    const canvas = this._canvas;

    // Transparent clear
    gl.clearColor(0, 0, 0, 0);
    gl.clear(gl.COLOR_BUFFER_BIT | gl.DEPTH_BUFFER_BIT);

    if (this._model && this._model._initialized) {
      const dpr = Math.min(window.devicePixelRatio || 1, 2);
      const W = canvas.width / dpr;
      const H = canvas.height / dpr;

      // Build projection: scale model to fit canvas height
      const modelMatrix = this._model.getModelMatrix();
      const scale = H / modelMatrix.getHeight();
      const projection = new CubismMatrix44();

      if (this._model._isFlipX) projection.scale(-scale, scale);
      else projection.scale(scale, scale);

      projection.translate(-modelMatrix.getCenterX(), -modelMatrix.getCenterY());

      this._model.update(dt);
      this._model.draw(projection);
    }

    this._rafId = requestAnimationFrame(() => this._renderLoop());
  }

  async playMotion(motionGroup) {
    if (!this._model) return;
    printMessage('[Live2D] playMotion: ' + motionGroup);
    await this._model.startRandomMotion(motionGroup, PRIORITY_NORMAL);
  }

  async setExpression(expression) {
    if (!this._model) return;
    printMessage('[Live2D] setExpression: ' + expression);
    this._model.setExpression(expression);
  }

  startSpeaking() {
    if (this._speaking) return;
    this._speaking = true;
    this.playMotion(MOTION_GROUP_TAP_BODY);
  }

  stopSpeaking() {
    if (!this._speaking) return;
    this._speaking = false;
    this.playMotion(MOTION_GROUP_IDLE);
  }

  setEnabled(visible) {
    this._canvas.style.display = visible ? 'block' : 'none';
  }

  dispose() {
    this._running = false;
    if (this._rafId !== null) {
      cancelAnimationFrame(this._rafId);
      this._rafId = null;
    }
    if (this._model) {
      const renderer = this._model.getRenderer();
      if (renderer) renderer.release();
      this._model = null;
    }
    CubismFramework.dispose();
  }
}

// ─── Factory ──────────────────────────────────────────────────────────────────

export function createLive2DAdapter(canvas) {
  return new Live2DAdapter(canvas);
}
