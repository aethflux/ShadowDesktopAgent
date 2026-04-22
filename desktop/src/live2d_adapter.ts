/**
 * Live2D Cubism 5 Adapter — bridges the Hoshino desktop pet UI to the
 * Live2D Cubism 5 SDK.
 *
 * Architecture
 * ────────────
 * The adapter is instantiated in ``pet.js`` (ES module) and replaces the static
 * CSS/HTML avatar when a Live2D model is configured.  It exposes the same
 * surface as the CSS pet so the rest of the UI (bubble, watchDot, speak…) is
 * unchanged.
 *
 * Interface that callers use:
 *   adapter.load()                    → Promise<void>
 *   adapter.playMotion(name)         → Promise<void>
 *   adapter.setExpression(name)      → Promise<void>
 *   adapter.startSpeaking()            → void
 *   adapter.stopSpeaking()             → void
 *   adapter.setEnabled(visible: boolean) → void
 *   adapter.dispose()                → void
 *
 * When the SDK fails to load (e.g. WebGL unavailable) all methods are no-ops
 * so the CSS avatar continues to work transparently.
 *
 * Model directory layout (drop your .model3.json here):
 *   live2d/
 *     Haru/
 *       Haru.model3.json   ← Cubism 5 model descriptor
 *       Haru.moc3          ← compiled model
 *       Haru.2048/         ← texture atlases
 *       expressions/        ← expression files
 *       motions/           ← motion files
 *       sounds/            ← audio for motions
 *
 * Triggering motions from the UI:
 *   Idle          → "Idle"        motion group  (plays on loop)
 *   Speaking      → "TapBody"     motion group
 *   Screen watch  → "TapBody"     motion group
 *   Celebration   → "TapBody"    motion group (random from group)
 *
 * NOTE: The actual Cubism 5 SDK files must be in desktop/live2d/:
 *   live2d/Core/live2dcubismcore.js
 *   live2d/Framework/dist/          ← Framework pre-built JS modules
 *   live2d/Haru/                    ← Haru model files
 */

export interface ILive2DAdapter {
  load(): Promise<void>;
  playMotion(motionGroup: string): Promise<void>;
  setExpression(expression: string): Promise<void>;
  startSpeaking(): void;
  stopSpeaking(): void;
  setEnabled(visible: boolean): void;
  dispose(): void;
}

/** Default adapter that falls back to the CSS pet (no-op for all Live2D calls). */
export class NoOpLive2DAdapter implements ILive2DAdapter {
  async load(): Promise<void> {}
  async playMotion(_name: string): Promise<void> {}
  async setExpression(_name: string): Promise<void> {}
  startSpeaking(): void {}
  stopSpeaking(): void {}
  setEnabled(_visible: boolean): void {}
  dispose(): void {}
}

/**
 * Real Live2D Cubism 5 adapter.
 *
 * Requires:
 *   1. desktop/live2d/Core/live2dcubismcore.js loaded as <script> before modules
 *   2. Import map resolving @framework/* to desktop/live2d/Framework/
 *   3. desktop/live2d/Haru/ model files
 *
 * Usage in pet.js:
 *   import { createLive2DAdapter } from 'live2d_adapter';
 *   const adapter = createLive2DAdapter(document.getElementById('live2d-canvas'));
 *   await adapter.load();
 */
export class Live2DAdapter implements ILive2DAdapter {
  private canvas: HTMLCanvasElement;
  private gl: WebGLRenderingContext | WebGL2RenderingContext | null = null;
  private model: any = null;
  private running = false;
  private rafId: number | null = null;
  private lastFrame = 0;
  private speaking = false;

  constructor(canvas: HTMLCanvasElement) {
    this.canvas = canvas;
  }

  async load(): Promise<void> {
    // Initialize WebGL
    this.gl = (this.canvas.getContext('webgl2') || this.canvas.getContext('webgl')) as WebGLRenderingContext | WebGL2RenderingContext | null;
    if (!this.gl) {
      console.warn('[Live2D] WebGL unavailable — CSS pet stays active');
      return;
    }

    const gl = this.gl;
    gl.enable(gl.BLEND);
    gl.blendFunc(gl.SRC_ALPHA, gl.ONE_MINUS_SRC_ALPHA);
    gl.enable(gl.DEPTH_TEST);
    gl.depthFunc(gl.LEQUAL);

    // Start render loop and load model...
    // (Full implementation in live2d_adapter.js ES module)
    this.running = true;
  }

  async playMotion(motionGroup: string): Promise<void> {}
  async setExpression(expression: string): Promise<void> {}

  startSpeaking(): void {
    if (this.speaking) return;
    this.speaking = true;
    this.playMotion('TapBody');
  }

  stopSpeaking(): void {
    if (!this.speaking) return;
    this.speaking = false;
    this.playMotion('Idle');
  }

  setEnabled(visible: boolean): void {
    this.canvas.style.display = visible ? 'block' : 'none';
  }

  dispose(): void {
    this.running = false;
    if (this.rafId !== null) {
      cancelAnimationFrame(this.rafId);
      this.rafId = null;
    }
  }
}

/** Factory function matching ILive2DAdapter interface. */
export function createLive2DAdapter(canvas: HTMLCanvasElement): ILive2DAdapter {
  return new Live2DAdapter(canvas);
}
