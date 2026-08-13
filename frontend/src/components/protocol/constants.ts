// n8n-style: the canvas opens a bit more zoomed out than 100% so a node (or
// the background grid) doesn't dominate the view the way a naive fitView
// does for one or two small nodes. Shared between ProtocolCanvas (initial
// viewport / fitView cap) and CanvasControls (the reset-zoom button's target).
export const DEFAULT_ZOOM = 0.75
