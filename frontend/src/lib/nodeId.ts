// crypto.randomUUID() only exists in a secure context (HTTPS or localhost) --
// throws in plain-HTTP dev setups reached via a LAN IP/forwarded hostname,
// which would silently abort node creation before setNodes ever ran. This
// has no such restriction. Shared by ProtocolCanvas.tsx's own "+" panel/
// import-merge flows and ConnectorAddStub's create-and-connect flow.
export function newNodeId(): string {
  return `node-${Date.now().toString(36)}-${Math.random().toString(36).slice(2, 10)}`
}
