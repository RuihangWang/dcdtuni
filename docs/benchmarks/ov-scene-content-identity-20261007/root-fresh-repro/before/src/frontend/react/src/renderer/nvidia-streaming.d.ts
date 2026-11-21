/** The NVIDIA package exports JS but omits its types condition. Narrow documented API. */
declare module '@nvidia/omniverse-webrtc-streaming-library' {
  export enum StreamStatus { none = 0, connecting = 1, connected = 2, cancelled = 3, error = 4 }
  export const AppStreamer: {
    readonly streamStatus: StreamStatus
    connect(options: { streamSource: string; streamConfig: Record<string, unknown> }): Promise<unknown>
    sendMessage(message: { event_type: string; payload: object }): Promise<unknown>
    terminate(terminateApp?: boolean): Promise<unknown>
  }
}
