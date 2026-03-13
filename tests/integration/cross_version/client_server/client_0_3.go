package main

import (
	"context"
	"fmt"
	"log"
	"os"
	"strings"

	"github.com/a2aproject/a2a-go/a2a"
	"github.com/a2aproject/a2a-go/a2aclient"
	"github.com/a2aproject/a2a-go/a2aclient/agentcard"
	"google.golang.org/grpc"
	"google.golang.org/grpc/credentials/insecure"
)

func boolPtr(b bool) *bool { return &b }
func intPtr(i int) *int    { return &i }

func testGetExtendedAgentCard(ctx context.Context, client *a2aclient.Client) {
	fmt.Println("Testing get_extended_agent_card...")
	card, err := client.GetAgentCard(ctx)
	if err != nil {
		log.Fatalf("client.GetAgentCard() error = %v", err)
	}

	if card == nil {
		log.Fatal("Card is nil")
	}

	fmt.Printf("DEBUG: Received card name: %s, additionalInterfaces: %d\n", card.Name, len(card.AdditionalInterfaces))

	if !strings.HasPrefix(card.Name, "Server") {
		log.Fatalf("Invalid card name: %s", card.Name)
	}

	if card.Version != "1.0.0" {
		log.Fatalf("Invalid version: %s", card.Version)
	}

	if !strings.Contains(card.Description, "running on a2a v") {
		log.Fatalf("Invalid description: %s", card.Description)
	}

	if !card.Capabilities.Streaming {
		log.Fatal("Streaming should be true")
	}
	if !card.Capabilities.PushNotifications {
		log.Fatal("Push notifications should be true")
	}

	fmt.Println("Success: get_extended_agent_card passed.")
}

func testSendMessageStream(ctx context.Context, client *a2aclient.Client) a2a.TaskID {
	fmt.Println("Testing send_message (streaming)...")

	msg := &a2a.MessageSendParams{
		Message: &a2a.Message{
			ID:   "stream-" + a2a.NewMessageID(),
			Role: a2a.MessageRoleUser,
			Parts: a2a.ContentParts{
				a2a.TextPart{Text: "stream"},
				a2a.FilePart{File: a2a.FileURI{URI: "https://example.com/file.txt", FileMeta: a2a.FileMeta{MimeType: "text/plain"}}},
				a2a.FilePart{File: a2a.FileBytes{Bytes: "aGVsbG8=", FileMeta: a2a.FileMeta{MimeType: "application/octet-stream"}}},
				a2a.DataPart{Data: map[string]any{"key": "value"}},
			},
			Metadata: map[string]any{"test_key": "full_message"},
		},
	}

	var taskID a2a.TaskID
	for event, err := range client.SendStreamingMessage(ctx, msg) {
		if err != nil {
			log.Fatalf("client.SendStreamingMessage() error = %v", err)
		}
		ti := event.TaskInfo()
		taskID = ti.TaskID
		if taskID != "" {
			break
		}
	}

	if taskID == "" {
		log.Fatal("Could not find task ID in first event")
	}

	fmt.Printf("Success: send_message (streaming) passed. Task ID: %s\n", taskID)
	return taskID
}

func testGetTask(ctx context.Context, client *a2aclient.Client, taskID a2a.TaskID) {
	fmt.Printf("Testing get_task (%s)...\n", taskID)
	task, err := client.GetTask(ctx, &a2a.TaskQueryParams{ID: taskID, HistoryLength: intPtr(10)})
	if err != nil {
		log.Fatalf("client.GetTask() error = %v", err)
	}

	if task.ID != taskID {
		log.Fatalf("Task ID mismatch: got %s, want %s", task.ID, taskID)
	}

	var userMsgs []*a2a.Message
	for _, m := range task.History {
		if m.Role == a2a.MessageRoleUser {
			userMsgs = append(userMsgs, m)
		}
	}

	if len(userMsgs) == 0 {
		log.Fatal("Expected at least one ROLE_USER message in task history")
	}

	clientMsg := userMsgs[0]
	parts := clientMsg.Parts
	if len(parts) != 4 {
		log.Fatalf("Expected 4 parts, got %d", len(parts))
	}

	if p, ok := parts[0].(a2a.TextPart); !ok || p.Text != "stream" {
		log.Fatalf("Part 0 mismatch: %v", parts[0])
	}

	if p, ok := parts[1].(a2a.FilePart); !ok {
		log.Fatalf("Part 1 is not FilePart: %T", parts[1])
	} else if fu, ok := p.File.(a2a.FileURI); !ok || fu.URI != "https://example.com/file.txt" {
		log.Fatalf("Part 1 URI mismatch: %v", p.File)
	}

	if p, ok := parts[2].(a2a.FilePart); !ok {
		log.Fatalf("Part 2 is not FilePart: %T", parts[2])
	} else if fb, ok := p.File.(a2a.FileBytes); !ok || fb.Bytes != "aGVsbG8=" {
		log.Fatalf("Part 2 Bytes mismatch: %v", p.File)
	}

	if p, ok := parts[3].(a2a.DataPart); !ok || p.Data["key"] != "value" {
		log.Fatalf("Part 3 Data mismatch: %v", parts[3])
	}

	fmt.Println("Success: get_task passed.")
}

func testSubscribe(ctx context.Context, client *a2aclient.Client, taskID a2a.TaskID) {
	fmt.Printf("Testing subscribe (%s)...\n", taskID)
	foundArtifact := false
	for event, err := range client.ResubscribeToTask(ctx, &a2a.TaskIDParams{ID: taskID}) {
		if err != nil {
			log.Fatalf("client.ResubscribeToTask() error = %v", err)
		}

		if artifactUpdate, ok := event.(*a2a.TaskArtifactUpdateEvent); ok {
			artifact := artifactUpdate.Artifact
			fmt.Printf("DEBUG: Received artifact: ID=%s, Name=%s\n", artifact.ID, artifact.Name)
			if artifact.Name != "test-artifact" {
				log.Fatalf("Artifact name mismatch: '%s' (ID=%s)", artifact.Name, artifact.ID)
			}
			if artifact.Metadata["artifact_key"] != "artifact_value" {
				log.Fatalf("Artifact metadata mismatch: %v", artifact.Metadata)
			}
			if len(artifact.Parts) == 0 {
				log.Fatal("Artifact has no parts")
			}
			if p, ok := artifact.Parts[0].(a2a.TextPart); !ok || p.Text != "artifact-chunk" {
				log.Fatalf("Artifact text mismatch: %v", artifact.Parts[0])
			}
			fmt.Println("Success: received artifact update.")
			foundArtifact = true
			break
		}
	}

	if !foundArtifact {
		log.Fatal("Did not receive artifact update")
	}

	fmt.Println("Success: subscribe passed.")
}

func testCancelTask(ctx context.Context, client *a2aclient.Client, taskID a2a.TaskID) {
	fmt.Printf("Testing cancel_task (%s)...\n", taskID)
	_, err := client.CancelTask(ctx, &a2a.TaskIDParams{ID: taskID})
	if err != nil {
		log.Fatalf("client.CancelTask() error = %v", err)
	}

	task, err := client.GetTask(ctx, &a2a.TaskQueryParams{ID: taskID})
	if err != nil {
		log.Fatalf("client.GetTask() error = %v", err)
	}

	if task.Status.State != a2a.TaskStateCanceled {
		log.Fatalf("Expected canceled state, got %s", task.Status.State)
	}

	fmt.Println("Success: cancel_task passed.")
}

func testSendMessageSync(ctx context.Context, client *a2aclient.Client) {
	fmt.Println("Testing send_message (synchronous)...")
	msg := &a2a.MessageSendParams{
		Config: &a2a.MessageSendConfig{
			Blocking: boolPtr(true),
		},
		Message: &a2a.Message{
			ID:   "sync-" + a2a.NewMessageID(),
			Role: a2a.MessageRoleUser,
			Parts: a2a.ContentParts{
				a2a.TextPart{Text: "sync"},
			},
			Metadata: map[string]any{"test_key": "simple_message"},
		},
	}

	result, err := client.SendMessage(ctx, msg)
	if err != nil {
		log.Fatalf("client.SendMessage() error = %v", err)
	}

	// If it's a task, check for completed status and metadata
	if task, ok := result.(*a2a.Task); ok {
		if !strings.HasSuffix(string(task.Status.State), "completed") {
			log.Fatalf("Task not completed: %s", task.Status.State)
		}
		statusMsg := task.Status.Message
		if statusMsg == nil {
			log.Fatal("TaskStatus message is missing")
		}

		if statusMsg.Metadata["response_key"] != "response_value" {
			log.Fatalf("Missing response metadata: %v", statusMsg.Metadata)
		}

		if len(statusMsg.Parts) == 0 {
			log.Fatal("No parts found in TaskStatus message")
		}
		if p, ok := statusMsg.Parts[0].(a2a.TextPart); !ok || p.Text != "done" {
			log.Fatalf("Expected 'done' text, got %v", statusMsg.Parts[0])
		}
	} else if _, ok := result.(*a2a.Message); ok {
		// OK, but we expected a task for blocking send against these servers
	} else {
		log.Fatalf("Unexpected SendMessage result type: %T", result)
	}

	fmt.Println("Success: send_message (synchronous) passed.")
}

func runClient(url string, protocol string) {
	ctx := context.Background()

	var transport a2a.TransportProtocol
	switch protocol {
	case "jsonrpc":
		transport = a2a.TransportProtocolJSONRPC
	case "grpc":
		transport = a2a.TransportProtocolGRPC
	case "rest":
		transport = a2a.TransportProtocolHTTPJSON
	default:
		log.Fatalf("Protocol %s not supported", protocol)
	}

	card, err := agentcard.DefaultResolver.Resolve(ctx, url)
	if err != nil {
		log.Fatalf("agentcard.Resolve() error = %v", err)
	}

	client, err := a2aclient.NewFromCard(ctx, card,
		a2aclient.WithGRPCTransport(grpc.WithTransportCredentials(insecure.NewCredentials())),
		a2aclient.WithConfig(a2aclient.Config{
			PreferredTransports: []a2a.TransportProtocol{transport},
		}))
	if err != nil {
		log.Fatalf("a2aclient.NewFromCard() error = %v", err)
	}
	defer client.Destroy()

	testGetExtendedAgentCard(ctx, client)
	taskID := testSendMessageStream(ctx, client)
	testGetTask(ctx, client, taskID)
	testSubscribe(ctx, client, taskID)
	testCancelTask(ctx, client, taskID)
	testSendMessageSync(ctx, client)
}

func main() {
	var url string
	var protocolList []string

	args := os.Args[1:]
	for i := 0; i < len(args); i++ {
		switch args[i] {
		case "--url":
			if i+1 < len(args) {
				url = args[i+1]
				i++
			}
		case "--protocols":
			for i+1 < len(args) && !strings.HasPrefix(args[i+1], "--") {
				protocolList = append(protocolList, args[i+1])
				i++
			}
		}
	}

	if url == "" {
		log.Fatal("Missing --url")
	}
	if len(protocolList) == 0 {
		log.Fatal("Missing --protocols")
	}

	failed := false
	for _, protocol := range protocolList {
		fmt.Printf("\n=== Testing protocol: %s ===\n", protocol)
		err := func() (err error) {
			defer func() {
				if r := recover(); r != nil {
					err = fmt.Errorf("panic: %v", r)
				}
			}()
			runClient(url, protocol)
			return nil
		}()
		if err != nil {
			fmt.Printf("FAILED protocol %s: %v\n", protocol, err)
			failed = true
		}
	}

	if failed {
		os.Exit(1)
	}
}
