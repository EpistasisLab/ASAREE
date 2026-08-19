// The canvas opens a bit more zoomed out than 100% so a node (or
// the background grid) doesn't dominate the view the way a naive fitView
// does for one or two small nodes. Shared between ProtocolCanvas (initial
// viewport / fitView cap) and CanvasControls (the reset-zoom button's target).
// 0.75 read as too far out -- two zoomIn() steps up from there (each step is
// xyflow's own 1.2x, confirmed in @xyflow/react's zoomIn implementation):
// 0.75 * 1.2 * 1.2 = 1.08.
export const DEFAULT_ZOOM = 1.08
