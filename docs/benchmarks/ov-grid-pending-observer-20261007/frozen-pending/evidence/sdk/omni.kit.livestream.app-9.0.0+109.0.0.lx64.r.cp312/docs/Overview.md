# Overview

The `omni.kit.livestream.app` extension enables streaming of the entire application framebuffer in Omniverse Kit applications. It captures rendered frames and streams them to remote clients using the streaming implementations provided by the `omni.kit.livestream.webrtc` extension.

## Purpose

This extension bridges the Omniverse Kit rendering pipeline with remote streaming clients by:

1. Capturing the application's framebuffer after rendering
2. Managing shared frame buffers for efficient streaming
3. Creating and configuring streaming servers
4. Supporting both primary and spectator streaming modes
5. Handling window resizing and frame synchronization

## Architecture

The `omni.kit.livestream.app` extension consists of these key components:

- **Extension**: Main entry point that initializes streaming and manages event subscriptions
- **SharedFrameBuffer**: Handles the capture and sharing of rendered frames for streaming
- **Settings Management**: Configures streaming parameters through Omniverse Kit's settings system

The extension integrates into the Omniverse Kit rendering pipeline by subscribing to key events:
- `PreUpdate`: For input handling and window resizing
- `PostEndRenderPass`: For synchronization with the rendering pipeline
- `PostPresentBuffer`: For capturing frames after they're presented

## Key Features

- **Application-Wide Streaming**: Captures and streams the entire application window
- **Multiple Stream Support**: Configurable primary stream with optional spectator streams
- **Dynamic Configuration**: Customizable stream parameters through settings
- **Efficient Frame Capture**: Direct CUDA buffer access for high-performance encoding
- **Synchronization**: Proper synchronization with the rendering pipeline
- **Port Management**: Automatic port allocation with collision detection and resolution

## Configuration Settings

The extension provides the following configurable settings:

### Primary Stream Settings

```cpp
exts."omni.kit.livestream.app".primaryStream.allowDynamicResize = false // Allow dynamic resizing of the video stream
exts."omni.kit.livestream.app".primaryStream.enableEventTracing = true  // Enable event trace logging for the stream
exts."omni.kit.livestream.app".primaryStream.enableOpenTelemetry = false// Enable/emit open telemetry for the stream
exts."omni.kit.livestream.app".primaryStream.publicIp = ""              // Fixed IP used for streaming media
exts."omni.kit.livestream.app".primaryStream.signalPort = 49100         // TCP port for connection negotiation
exts."omni.kit.livestream.app".primaryStream.streamPort = 47998         // UDP port for streaming media
exts."omni.kit.livestream.app".primaryStream.streamType = "webrtc"      // Stream type (webrtc or native)
exts."omni.kit.livestream.app".primaryStream.targetFps = 60             // Target frames per second
```

### Spectator Stream Settings

```cpp
exts."omni.kit.livestream.app".spectatorStream.0.streamType = "webrtc"  // Stream type (webrtc or native)
exts."omni.kit.livestream.app".spectatorStream.0.signalPort = 49200     // TCP port for connection negotiation
exts."omni.kit.livestream.app".spectatorStream.0.streamPort = 47999     // UDP port for streaming media
```

## Usage

### Creating a Primary Stream

The extension automatically creates a primary stream at startup if the `streamType` setting is defined. You can modify these settings in your application's `.kit` file:

```
[settings]
exts."omni.kit.livestream.app".primaryStream.allowDynamicResize = true
exts."omni.kit.livestream.app".primaryStream.streamPort = 47999
exts."omni.kit.livestream.app".primaryStream.streamType = "native"
```

using arguments when running your application:

```
--/exts/omni.kit.livestream.app/primaryStream/allowDynamicResize=true
--/exts/omni.kit.livestream.app/primaryStream/streamPort=47999
--/exts/omni.kit.livestream.app/primaryStream/streamType="native"
```

or programmatically:

```cpp
carb::settings::ISettings* settings = carb::getCachedInterface<carb::settings::ISettings>();
settings->setString("exts/omni.kit.livestream.app/primaryStream/allowDynamicResize", true);
settings->setString("exts/omni.kit.livestream.app/primaryStream/streamType", "native");
settings->setUInt16("exts/omni.kit.livestream.app/primaryStream/streamPort", 47999);
```

### Creating Spectator Streams

You can create multiple spectator streams by defining their settings in the same ways:

```
[settings]
exts."omni.kit.livestream.app".spectatorStream.0.streamType = "webrtc"
exts."omni.kit.livestream.app".spectatorStream.0.signalPort = 49200
exts."omni.kit.livestream.app".spectatorStream.0.streamPort = 48000

exts."omni.kit.livestream.app".spectatorStream.1.streamType = "native"
exts."omni.kit.livestream.app".spectatorStream.1.signalPort = 49300
exts."omni.kit.livestream.app".spectatorStream.1.streamPort = 48002
```

```
--/exts/omni.kit.livestream.app/spectatorStream/0/streamType="webrtc"
--/exts/omni.kit.livestream.app/spectatorStream/0/signalPort=49200
--/exts/omni.kit.livestream.app/spectatorStream/0/streamPort=47999
--/exts/omni.kit.livestream.app/spectatorStream/1/streamType="native"
--/exts/omni.kit.livestream.app/spectatorStream/1/signalPort=49300
--/exts/omni.kit.livestream.app/spectatorStream/1/streamPort=48000
```

```cpp
// Set up the first spectator stream
settings->setString("exts/omni.kit.livestream.app/spectatorStream/0/streamType", "webrtc");
settings->setUInt16("exts/omni.kit.livestream.app/spectatorStream/0/signalPort", 49200);
settings->setUInt16("exts/omni.kit.livestream.app/spectatorStream/0/streamPort", 47999);

// Set up the second spectator stream
settings->setString("exts/omni.kit.livestream.app/spectatorStream/1/streamType", "native");
settings->setUInt16("exts/omni.kit.livestream.app/spectatorStream/1/signalPort", 49300);
settings->setUInt16("exts/omni.kit.livestream.app/spectatorStream/1/streamPort", 48000);
```

## Implementation Details

### Frame Capture Process

The frame capture and streaming process follows these steps:

1. During `onPostEndRenderPass`, the extension prepares/cycles shared frame buffers and initiates copying of the framebuffer
2. During `onPostPresentBuffer`, it waits for the copy of the framebuffer to complete, synchronizing with the GPU to ensure frame integrity
3. Completed frames are streamed to all active servers

### Stream Server Management

The extension manages streaming servers as follows:

1. By default, it creates the primary stream server if configured correctly
2. It dynamically creates or updates spectator stream servers based on settings
3. Each server gets its own unique ports to avoid conflicts
4. All servers receive the same frame data but can have different clients

### Window Resizing

Window resizing is handled through the `resizeAppWindow` method, which:

1. Queries the primary stream for the desired stream resolution
2. Resizes the application window to match the desired stream resolution
3. Shared frame buffers are recreated internally if needed to accommodate the new size

## Integration with Other Extensions

The `omni.kit.livestream.app` extension:

- Depends on `omni.kit.livestream.webrtc` for the streaming implementations
- Depends on `omni.kit.renderer.core` for frame capture and rendering pipeline integration
- Works with `omni.kit.livestream.messaging` for structured message exchange
- Works with `omni.services.livestream.session` for session management

## Performance Considerations

For optimal streaming performance:

- Consider the impact of the target frame rate on CPU and GPU utilization
- Be cautious with dynamic resizing as it may impact performance
- When using spectator streams, ensure each has unique port assignments

## Version History

The extension follows semantic versioning and maintains a detailed changelog. This extension was branched from `omni.kit.livestream.core` in version 8.0.0.

For detailed change history, refer to the [CHANGELOG.md](CHANGELOG.md) file.
