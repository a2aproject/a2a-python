import { ClientFactory, ClientFactoryOptions } from '@a2a-js/sdk/client';
import { GrpcTransportFactory } from '@a2a-js/sdk/client/grpc';
import { randomUUID } from 'crypto';

async function testGetExtendedAgentCard(client) {
    console.log('Testing get_extended_agent_card...');
    const card = await client.getAgentCard();
    if (!card) throw new Error("Card is null");
    if (!card.name.startsWith('Server')) throw new Error("Invalid card name: " + card.name);
    if (card.version !== '1.0.0') throw new Error("Invalid version");
    if (!card.description.includes('Server running on a2a v')) throw new Error("Invalid description");

    if (!card.capabilities) throw new Error("No capabilities");
    if (card.capabilities.streaming !== true) throw new Error("Streaming should be true");
    if (card.capabilities.pushNotifications !== true) throw new Error("Push notifications should be true");

    if (card.name.startsWith('Server 0.3')) {
        if (!card.url) throw new Error("No url");
        if (card.preferredTransport !== 'JSONRPC') throw new Error("preferredTransport must be JSONRPC");
        if (card.additionalInterfaces.length !== 2) throw new Error("Should have 2 additionalInterfaces");
        if (card.supportsAuthenticatedExtendedCard !== false) throw new Error("Should not support auth ext card");
    } else {
        if (!card.url) throw new Error("No url");
        if (!card.preferredTransport) throw new Error("No preferredTransport");
        if (card.supportsAuthenticatedExtendedCard !== false && card.supportsAuthenticatedExtendedCard !== undefined) {
             throw new Error("Invalid supportsAuthenticatedExtendedCard");
        }
    }
    console.log('Success: get_extended_agent_card passed.');
}

function encodeBase64(s, times) {
    let result = s;
    for (let i = 0; i < times; i++) {
        result = Buffer.from(result).toString('base64');
    }
    return result;
}

function getEncodingTimes(protocol) {
    // XXX: Inconsistent base64 encoding between transports.
    // gRPC requires double encoding because JS SDK decodes once before sending,
    // and Python 0.3 server expects base64 string in bytes field.
    return protocol === 'grpc' ? 2 : 1;
}

async function testSendMessageStream(client, protocol) {
    console.log('Testing send_message (streaming)...');
    
    const testBytes = encodeBase64('hello', getEncodingTimes(protocol));

    // In JS SDK, client.sendMessageStream returns an async iterable of events
    const stream = client.sendMessageStream({
        message: {
            messageId: `stream-${randomUUID()}`,
            role: 'user',
            kind: 'message',
            parts: [
                { kind: 'text', text: 'stream' },
                { kind: 'file', file: { uri: 'https://example.com/file.txt', mimeType: 'text/plain' } },
                { kind: 'file', file: { bytes: testBytes, mimeType: 'application/octet-stream' } },
                { kind: 'data', data: { key: 'value' } }
            ],
            metadata: { test_key: 'full_message' }
        }
    });

    const events = [];
    for await (const event of stream) {
        events.push(event);
        break; // just get the first event
    }

    if (events.length === 0) throw new Error('Expected at least one event');
    const firstEvent = events[0];
    
    const taskId = firstEvent.id || firstEvent.taskId;
    if (!taskId) throw new Error('Could not find task ID in first event');

    console.log(`Success: send_message (streaming) passed. Task ID: ${taskId}`);
    return taskId;
}

async function testGetTask(client, taskId, protocol) {
    console.log(`Testing get_task (${taskId})...`);
    const task = await client.getTask({ id: taskId });
    if (task.id !== taskId) throw new Error("Task ID mismatch");

    const userMsgs = task.history.filter(m => m.role === 'user');
    if (userMsgs.length === 0) throw new Error("Expected at least one ROLE_USER message in task history");

    const clientMsg = userMsgs[0];
    const parts = clientMsg.parts;
    if (parts.length !== 4) throw new Error(`Expected 4 parts, got ${parts.length}`);

    if (parts[0].text !== 'stream') throw new Error(`Expected 'stream', got ${parts[0].text}`);
    if (parts[1].file.uri !== 'https://example.com/file.txt') throw new Error("URI mismatch");
    
    const expectedBytes = encodeBase64('hello', getEncodingTimes(protocol));
    if (parts[2].file.bytes !== expectedBytes) throw new Error("Bytes mismatch");

    // Deep equal for data
    if (JSON.stringify(parts[3].data) !== JSON.stringify({ key: 'value' })) throw new Error("Data mismatch");

    console.log('Success: get_task passed.');
}

async function testCancelTask(client, taskId) {
    console.log(`Testing cancel_task (${taskId})...`);
    await client.cancelTask({ id: taskId });
    const task = await client.getTask({ id: taskId });
    if (task.status.state !== 'canceled') throw new Error(`Expected a canceled state, got ${task.status.state}`);
    console.log('Success: cancel_task passed.');
}

async function testSubscribe(client, taskId) {
    console.log(`Testing subscribe (${taskId})...`);
    let hasArtifact = false;
    const stream = client.resubscribeTask({ id: taskId });
    for await (const event of stream) {
        if (event.kind === 'artifact-update' || (event.update && event.update.artifact)) {
            hasArtifact = true;
            const artifact = event.artifact || event.update.artifact;
            if (artifact.name !== 'test-artifact') throw new Error("Artifact name mismatch");
            if (artifact.metadata.artifact_key !== 'artifact_value') throw new Error("Artifact metadata mismatch");
            if (artifact.parts[0].text !== 'artifact-chunk') throw new Error("Artifact text mismatch");
            console.log('Success: received artifact update.');
        }
        if (hasArtifact) break;
    }
    console.log('Success: subscribe passed.');
}

async function testSendMessageSync(client, protocol) {
    console.log('Testing send_message (synchronous)...');
    
    const result = await client.sendMessage({
        message: {
            messageId: `sync-${randomUUID()}`,
            role: 'user',
            kind: 'message',
            parts: [{ kind: 'text', text: 'sync' }],
            metadata: { test_key: 'simple_message' }
        },
        configuration: {
            blocking: true, // Wait for completion if possible
            streaming: false
        }
    });

    // Handle return
    let statusMsg;
    if (result.kind === 'task') {
        const status = result.status;
        if (status.state && status.state.endsWith('completed')) {
            statusMsg = status.message || status.update;
        }
    } else {
        // if it just returns message directly
        statusMsg = result;
    }

    if (!statusMsg) {
        throw new Error("Did not receive completed status message or final message");
    }

    if (statusMsg.metadata && statusMsg.metadata.response_key !== 'response_value') {
         throw new Error(`Missing response metadata: ${JSON.stringify(statusMsg.metadata)}`);
    }

    const parts = statusMsg.parts || statusMsg.content || [];
    if (parts.length === 0) throw new Error("No parts found in TaskStatus message");
    if (parts[0].text !== 'done') throw new Error(`Expected 'done' text in Part, got '${parts[0].text}'`);

    console.log('Success: send_message (synchronous) passed.');
}

async function runClient(url, protocol) {
    const protocolMap = {
        'jsonrpc': 'JSONRPC',
        'rest': 'HTTP+JSON',
        'grpc': 'GRPC'
    };

    const preferredTransport = protocolMap[protocol];
    if (!preferredTransport) {
        throw new Error(`Protocol ${protocol} not supported by this JS client script.`);
    }

    const factory = new ClientFactory({
        ...ClientFactoryOptions.default,
        transports: [
            ...ClientFactoryOptions.default.transports,
            new GrpcTransportFactory()
        ],
        preferredTransports: [preferredTransport]
    });
    const client = await factory.createFromUrl(url);

    await testGetExtendedAgentCard(client);
    const taskId = await testSendMessageStream(client, protocol);
    await testGetTask(client, taskId, protocol);
    await testSubscribe(client, taskId);
    await testCancelTask(client, taskId);
    await testSendMessageSync(client, protocol);
}

async function main() {
    console.log('Starting client_0_3.mjs...');
    
    const args = process.argv.slice(2);
    let url = '';
    let protocols = [];
    
    for (let i = 0; i < args.length; i++) {
        if (args[i] === '--url') {
            url = args[++i];
        } else if (args[i] === '--protocols') {
            while (i + 1 < args.length && !args[i+1].startsWith('--')) {
                protocols.push(args[++i]);
            }
        }
    }

    if (!url) {
        console.error("Missing --url argument");
        process.exit(1);
    }
    
    if (protocols.length === 0) {
        console.error("Missing --protocols arguments");
        process.exit(1);
    }

    let failed = false;
    for (const protocol of protocols) {
        console.log(`\n=== Testing protocol: ${protocol} ===`);
        try {
            await runClient(url, protocol);
        } catch (e) {
            console.error(`FAILED protocol ${protocol}:`, e);
            failed = true;
        }
    }

    if (failed) {
        process.exit(1);
    } else {
        process.exit(0);
    }
}

main().catch(e => {
    console.error(e);
    process.exit(1);
});
