// live2d_framework_shim.js
// Re-exports everything from the Live2D Cubism 5 Framework dist bundle.
// The import map routes @framework/* here; internal imports resolve via
// their own relative paths within the Framework/dist/ directory.
export * from '../live2d/Framework/dist/live2dcubismframework.js';
