# Overview

The `omni.kit.livestream.core` extension serves as the foundation for the Omniverse Kit livestreaming capabilities. It provides a server factory infrastructure that enables the creation and management of different types of streaming servers within the Omniverse ecosystem.

## Purpose

This extension acts as a central hub for streaming functionality, allowing other extensions to register and create various types of streaming servers (such as WebRTC or proprietary native implementations) through a consistent interface. The core extension itself does not implement any specific streaming protocol but instead provides the architectural framework for implementing and managing streaming solutions.

## Architecture

The `omni.kit.livestream.core` extension is built around a server factory pattern, consisting of:

- **IServerFactory Interface**: Provides methods to register, deregister, and create streaming server instances based on type.
- **ServerFactory Implementation**: Implements the factory interface to manage registration of different server types and create instances on demand.
- **IServer Interface**: Defines the common interface that all streaming server implementations must follow, enabling consistent handling regardless of the underlying streaming technology.

## Key Features

- **Server Factory Pattern**: Enables extensions to register and create different types of streaming servers.
- **Protocol Agnostic**: Works with various streaming protocols (WebRTC, native, etc.) through a unified interface.
- **Performance Optimization**: Includes default settings tuned for optimal streaming performance.
- **Extensible Design**: Allows new streaming implementations to be added without modifying the core infrastructure.

## Usage

### Creating a Streaming Server

To create a streaming server in C++:

1. Acquire the server factory interface:
```cpp
auto serverFactory = carb::getCachedInterface<omni::kit::livestream::IServerFactory>();
```

2. Create a server of the desired type:
```cpp
// Create a WebRTC server
auto server = serverFactory->createServer("webrtc");

// Configure and start the server
omni::kit::livestream::IServer::Config config;
config.width = 1920;
config.height = 1080;
config.targetFPS = 60;
server->start(config);
```

### Implementing a Custom Streaming Server

To implement a custom streaming server:

1. Create a class that implements the `IServer` interface.
2. Register a factory function with the server factory:
```cpp
serverFactory->registerFactoryFunction("my_stream_type", MyServer::create);
```

3. Users can then create instances of your server using the factory:
```cpp
auto server = serverFactory->createServer("my_stream_type");
```

## Integration with Other Extensions

The `omni.kit.livestream.core` extension integrates with:

- **omni.kit.livestream.webrtc**: Provides WebRTC and native streaming implementations.
- **omni.kit.livestream.app**: Handles streaming the entire application framebuffer.
- **omni.kit.livestream.aov**: Handles streaming specific render product AOVs.
- **omni.kit.livestream.messaging**: Enables structured messaging between server and clients.
- **omni.services.livestream.session**: Manages livestream sessions and provides control endpoints.

## Performance Considerations

The extension includes default settings optimized for streaming performance, such as:

- Consistent frame rates with configurable targeting
- Optimized run loop and rendering configurations
- Synchronization settings to ensure smooth video delivery

These settings can be adjusted through the Omniverse Kit settings system according to specific application requirements.

## Version History

The extension follows semantic versioning and maintains a detailed changelog. Major updates typically align with new Kit versions, while minor updates include feature enhancements and bug fixes.

For detailed change history, refer to the [CHANGELOG.md](CHANGELOG.md) file.
