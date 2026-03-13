import express from 'express';
import http from 'http';
import * as grpc from '@grpc/grpc-js';
import { v4 as uuidv4 } from 'uuid';
import { 
    DefaultRequestHandler, 
    InMemoryTaskStore, 
    DefaultExecutionEventBusManager
} from '@a2a-js/sdk/server';
import { jsonRpcHandler, agentCardHandler, restHandler, UserBuilder } from '@a2a-js/sdk/server/express';
import { grpcService, A2AService, UserBuilder as GrpcUserBuilder } from '@a2a-js/sdk/server/grpc';

class MockAgentExecutor {
    constructor() {
        this.events = new Map();
        this.taskContexts = new Map();
    }

    async execute(requestContext, eventBus) {
        const taskId = requestContext.taskId;
        const contextId = requestContext.contextId;
        this.taskContexts.set(taskId, contextId);
        console.log(`SERVER: execute called for task ${taskId}`);

        // Initial task event to register it in ResultManager
        console.log(`SERVER: publishing task event with history length 1`);
        eventBus.publish({
            kind: 'task',
            id: taskId,
            contextId: contextId,
            status: { state: 'submitted' },
            history: [requestContext.userMessage]
        });

        // status update to working
        eventBus.publish({
            kind: 'status-update',
            taskId: taskId,
            contextId: contextId,
            status: { state: 'working' },
            final: false
        });

        const userMessage = requestContext.userMessage;
        const metadata = userMessage.metadata || {};

        if (metadata.test_key !== 'full_message' && metadata.test_key !== 'simple_message') {
            console.log(`SERVER: WARNING: Missing or incorrect metadata: ${JSON.stringify(metadata)}`);
            eventBus.publish({
                kind: 'status-update',
                taskId: taskId,
                contextId: contextId,
                status: { 
                    state: 'failed', 
                    message: { 
                        messageId: uuidv4(), 
                        role: 'agent', 
                        kind: 'message', 
                        parts: [{kind: 'text', text: 'Invalid metadata'}],
                        taskId: taskId,
                        contextId: contextId
                    } 
                },
                final: true
            });
            eventBus.finished();
            return;
        }

        const parts = userMessage.parts || [];
        const text = parts.length > 0 && parts[0].kind === 'text' ? parts[0].text : '';

        if (metadata.test_key === 'full_message') {
            try {
                if (parts.length !== 4) throw new Error(`Expected 4 parts, got ${parts.length}`);
                if (parts[0].text !== 'stream') throw new Error(`Expected 'stream', got ${parts[0].text}`);
                if (parts[1].file.uri !== 'https://example.com/file.txt') throw new Error("URI mismatch");

                const receivedBytes = parts[2].file.bytes;
                // XXX: Inconsistent base64 encoding between transports.
                // 'aGVsbG8=' is 'hello' base64 encoded once.
                // 'YUdWc2JHOD0=' is 'hello' base64 encoded twice (Buffer.from('aGVsbG8=').toString('base64')).
                if (receivedBytes !== 'aGVsbG8=' && receivedBytes !== 'YUdWc2JHOD0=') {
                    throw new Error(`Bytes mismatch: got '${receivedBytes}'`);
                }

                if (JSON.stringify(parts[3].data) !== JSON.stringify({ key: 'value' })) throw new Error("Data mismatch");
            } catch (e) {
                console.log(`SERVER: Error in part validation: ${e.message}`);
                 eventBus.publish({
                    kind: 'status-update',
                    taskId: taskId,
                    contextId: contextId,
                    status: { 
                        state: 'failed', 
                        message: { 
                            messageId: uuidv4(), 
                            role: 'agent', 
                            kind: 'message', 
                            parts: [{kind: 'text', text: e.message}],
                            taskId: taskId,
                            contextId: contextId
                        } 
                    },
                    final: true
                });
                eventBus.finished();
                return;
            }
        }

        console.log(`SERVER: request message text='${text}'`);

        if (text.includes('stream')) {
            console.log(`SERVER: waiting on stream event for task ${taskId}`);
            let resolveEvent;
            const eventPromise = new Promise(resolve => { resolveEvent = resolve; });
            this.events.set(taskId, resolveEvent);

            const interval = setInterval(() => {
                eventBus.publish({
                    kind: 'status-update',
                    taskId: taskId,
                    contextId: contextId,
                    status: {
                        state: 'working',
                        message: {
                            messageId: uuidv4(),
                            role: 'agent',
                            kind: 'message',
                            parts: [{ kind: 'text', text: 'ping' }],
                            taskId: taskId,
                            contextId: contextId
                        }
                    },
                    final: false
                });

                eventBus.publish({
                    kind: 'artifact-update',
                    taskId: taskId,
                    contextId: contextId,
                    artifact: {
                        artifactId: uuidv4(),
                        name: 'test-artifact',
                        parts: [{ kind: 'text', text: 'artifact-chunk' }],
                        metadata: { artifact_key: 'artifact_value' }
                    }
                });
            }, 100);

            await eventPromise;
            clearInterval(interval);
            console.log(`SERVER: stream event triggered for task ${taskId}`);
        }

        eventBus.publish({
            kind: 'status-update',
            taskId: taskId,
            contextId: contextId,
            status: {
                state: 'completed',
                message: {
                    messageId: uuidv4(),
                    role: 'agent',
                    kind: 'message',
                    parts: [{ kind: 'text', text: 'done' }],
                    metadata: { response_key: 'response_value' },
                    taskId: taskId,
                    contextId: contextId
                }
            },
            final: true
        });
        console.log(`SERVER: execute finished for task ${taskId}`);
        eventBus.finished();
    }

    async cancelTask(taskId, eventBus) {
        console.log(`SERVER: cancel called for task ${taskId}`);
        const resolve = this.events.get(taskId);
        if (resolve) {
            resolve();
            this.events.delete(taskId);
        }

        const contextId = this.taskContexts.get(taskId) || 'unknown';
        eventBus.publish({
            kind: 'status-update',
            taskId: taskId,
            contextId: contextId,
            status: { state: 'canceled' },
            final: true
        });
        eventBus.finished();
    }
}

async function main() {
    console.log('Starting server_0_3.mjs...');
    const args = process.argv.slice(2);
    let httpPort = 0;
    let grpcPort = 0;
    for (let i = 0; i < args.length; i++) {
        if (args[i] === '--http-port') {
            httpPort = parseInt(args[++i]);
        } else if (args[i] === '--grpc-port') {
            grpcPort = parseInt(args[++i]);
        }
    }

    if (!httpPort) {
        console.error("Missing --http-port");
        process.exit(1);
    }
    if (!grpcPort) {
        console.error("Missing --grpc-port");
        process.exit(1);
    }

    const agentCard = {
        name: 'Server 0.3 (JS)',
        description: 'Server running on a2a v0.3.0',
        version: '1.0.0',
        url: `http://127.0.0.1:${httpPort}/jsonrpc/`,
        preferredTransport: 'JSONRPC',
        capabilities: { streaming: true, pushNotifications: true },
        skills: [],
        defaultInputModes: ['text/plain'],
        defaultOutputModes: ['text/plain'],
        additionalInterfaces: [
            {
                transport: 'HTTP+JSON',
                url: `http://127.0.0.1:${httpPort}/rest/`
            },
            {
                transport: 'GRPC',
                url: `127.0.0.1:${grpcPort}`
            }
        ],
        supportsAuthenticatedExtendedCard: false
    };

    const handler = new DefaultRequestHandler(
        agentCard,
        new InMemoryTaskStore(),
        new MockAgentExecutor(),
        new DefaultExecutionEventBusManager()
    );

    // Patch handler.getTask to return history if historyLength is not positive
    const originalGetTask = handler.getTask.bind(handler);
    handler.getTask = async (params, context) => {
        console.log(`SERVER: getTask called for ${params.id}, historyLength=${params.historyLength}`);
        const result = await originalGetTask(params, context);
        // XXX: Default is empty history vs python is 100.
        if ((params.historyLength === undefined || params.historyLength <= 0) && (!result.history || result.history.length === 0)) {
            const allHistoryTask = await originalGetTask({ ...params, historyLength: 100 }, context);
            return allHistoryTask;
        }
        return result;
    };

    // XXX Patch handler.getAuthenticatedExtendedAgentCard to support Python 1.0 client over gRPC
    handler.getAuthenticatedExtendedAgentCard = async (context) => {
        console.log('SERVER: getAuthenticatedExtendedAgentCard called');
        return agentCard;
    };

    // Start HTTP/JSON-RPC and REST Server
    const app = express();
    app.use(express.json());
    app.use('/jsonrpc', jsonRpcHandler({ 
        requestHandler: handler,
        userBuilder: UserBuilder.noAuthentication
    }));
    app.use('/rest', restHandler({
        requestHandler: handler,
        userBuilder: UserBuilder.noAuthentication
    }));
    // Standard path for agent card
    // XXX Why both ?
    app.use('/jsonrpc/.well-known/agent-card.json', agentCardHandler({ agentCardProvider: handler }));
    app.use('/.well-known/agent-card.json', agentCardHandler({ agentCardProvider: handler }));

    const server = http.createServer(app);
    server.listen(httpPort, '127.0.0.1', () => {
        console.log(`SERVER: Starting JSON-RPC and REST server on http_port=${httpPort}`);
    });

    // Start gRPC Server
    const grpcServer = new grpc.Server();
    grpcServer.addService(
        A2AService,
        grpcService({
            requestHandler: handler,
            userBuilder: GrpcUserBuilder.noAuthentication
        })
    );
    grpcServer.bindAsync(`127.0.0.1:${grpcPort}`, grpc.ServerCredentials.createInsecure(), (err, port) => {
        if (err) {
            console.error(`Failed to bind gRPC server: ${err.message}`);
            process.exit(1);
        }
        grpcServer.start();
        console.log(`SERVER READY`);
        console.log(`SERVER: Starting gRPC server on grpc_port=${grpcPort}`);
    });
}

main().catch(console.error);
