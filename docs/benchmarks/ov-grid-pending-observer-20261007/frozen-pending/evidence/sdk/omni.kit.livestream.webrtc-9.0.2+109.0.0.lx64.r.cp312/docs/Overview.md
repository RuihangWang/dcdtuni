# Overview

The `omni.kit.livestream.webrtc` extension provides implementations of the streaming server interface (`IServer`) for both WebRTC and native protocols. It enables streaming video frames from CUDA buffers to remote clients with support for bidirectional messaging and input handling.

## Purpose

This extension bridges the Omniverse Kit rendering pipeline with remote clients by:

1. Registering WebRTC and native streaming implementations with the `omni.kit.livestream.core` server factory
2. Providing efficient CUDA-based video frame encoding and streaming
3. Handling client input and message events
4. Managing streaming sessions and connection state

## Architecture

The extension consists of several key components:

- **Server**: Implements the `IServer` interface for both WebRTC and native protocols
- **InputHandler**: Processes and translates input events from the client
- **MessageHandler**: Handles custom messages between server and client
- **SessionHandler**: Manages streaming sessions and STUN credentials

These components work together to provide a complete streaming solution built on NVIDIA's StreamSDK technology.

## Key Features

- **Multiple Protocol Support**: Implements both WebRTC and native streaming protocols
- **CUDA Integration**: Direct streaming from CUDA buffers for high-performance encoding
- **Bidirectional Communication**: Support for sending and receiving messages between server and client
- **Input Handling**: Processing of keyboard, mouse, and gamepad input from clients
- **Dynamic Resizing**: Ability to resize streams both before and after client connections
- **Session Management**: Control over streaming sessions and STUN credentials

## Usage

### Creating and Using a WebRTC Server

```cpp
// Get the server factory interface
auto serverFactory = carb::getCachedInterface<omni::kit::livestream::IServerFactory>();

// Create a WebRTC server
auto server = serverFactory->createServer("webrtc");

// Configure the server
omni::kit::livestream::IServer::Config config;
config.width = 1920;
config.height = 1080;
config.targetFPS = 60;
config.signalPort = 49100;
config.streamPort = 47999;
config.publicIp = ""; // Leave empty to use ICE for NAT traversal
config.allowDynamicResize = false;

// Start the server
server->start(config);

// Stream a frame from a CUDA buffer
server->streamVideoCudaBuffer(cudaBuffer, pitchBytes, width, height);

// Send a message to the client
server->sendMessageToClient("Hello from server");

// Stop the server when done
server->stop();
```

### Creating a Native Protocol Server

```cpp
// Create a native protocol server
auto nativeServer = serverFactory->createServer("native");

// Configure and use the server the same way as WebRTC
// ...
```

## Implementation Details

### Server Factory Registration

During extension startup, the server implementations are registered with the server factory:

```cpp
// Register the WebRTC server implementation
serverFactory->registerFactoryFunction(Server::kStreamTypeWebRTC, Server::createWebRTC);

// Register the native server implementation
serverFactory->registerFactoryFunction(Server::kStreamTypeNative, Server::createNative);
```

### Stream Types

The server manages three types of streams:

1. **Video Stream**: For streaming video frames from CUDA buffers
2. **Audio Stream**: For streaming audio data (when enabled)
3. **Input Stream**: For receiving input events from the client

### Resizing Capabilities

The server supports two types of resizing:

- **Static Resizing**: Before client connection (via `resizeVideoStreamStatic`)
- **Dynamic Resizing**: After client connection (via `resizeVideoStreamDynamic`)

### Client Connection Events

The server dispatches events when clients connect or disconnect:

- **Client Connected**: Dispatched when a client connects to the server
- **Client Disconnected**: Dispatched when a client disconnects from the server

## Integration with Other Extensions

The `omni.kit.livestream.webrtc` extension:

- Depends on `omni.kit.livestream.core` for the server factory infrastructure
- Provides streaming implementation for `omni.kit.livestream.app` and `omni.kit.livestream.aov`
- Works with `omni.kit.livestream.messaging` for structured message exchange
- Works with `omni.services.livestream.session` for session management

## Performance Considerations

For optimal streaming performance:

- Use CUDA buffers directly when possible to avoid unnecessary copies
- Consider the trade-offs between resolution, frame rate, and bandwidth
- Be aware that dynamic resizing can impact performance
- Use the native protocol when lower latency is more important than web compatibility (requires a native streaming client)

## Version History

The extension follows semantic versioning and maintains a detailed changelog. Major updates typically align with new Kit versions, while minor updates include feature enhancements and bug fixes.

For detailed change history, refer to the [CHANGELOG.md](CHANGELOG.md) file.
