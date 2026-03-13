package main

import (
	"context"
	"encoding/json"
	"flag"
	"fmt"
	"log"
	"net"
	"net/http"
	"strings"
	"sync"
	"time"

	"github.com/a2aproject/a2a-go/a2a"
	"github.com/a2aproject/a2a-go/a2agrpc"
	"github.com/a2aproject/a2a-go/a2asrv"
	"github.com/a2aproject/a2a-go/a2asrv/eventqueue"
	"google.golang.org/grpc"
)

type mockAgentExecutor struct {
	mu           sync.Mutex
	cancelChans  map[a2a.TaskID]chan struct{}
	taskContexts map[a2a.TaskID]string
}

func newMockAgentExecutor() *mockAgentExecutor {
	return &mockAgentExecutor{
		cancelChans:  make(map[a2a.TaskID]chan struct{}),
		taskContexts: make(map[a2a.TaskID]string),
	}
}

func (e *mockAgentExecutor) Execute(ctx context.Context, reqCtx *a2asrv.RequestContext, q eventqueue.Queue) error {
	taskID := reqCtx.TaskID
	contextID := reqCtx.ContextID

	e.mu.Lock()
	cancelChan := make(chan struct{})
	e.cancelChans[taskID] = cancelChan
	e.taskContexts[taskID] = contextID
	e.mu.Unlock()

	defer func() {
		e.mu.Lock()
		delete(e.cancelChans, taskID)
		e.mu.Unlock()
	}()

	log.Printf("SERVER: execute called for task %s", taskID)

	// Go SDK handles task persistence in defaultRequestHandler.Execute.
	// We just need to report progress.
	q.Write(ctx, a2a.NewStatusUpdateEvent(reqCtx, a2a.TaskStateWorking, nil))

	userMsg := reqCtx.Message
	metadata := userMsg.Metadata

	if metadata["test_key"] != "full_message" && metadata["test_key"] != "simple_message" {
		log.Printf("SERVER: WARNING: Missing or incorrect metadata: %v", metadata)
		errMsg := a2a.NewMessage(a2a.MessageRoleAgent, a2a.TextPart{Text: "Invalid metadata"})
		statusEvent := a2a.NewStatusUpdateEvent(reqCtx, a2a.TaskStateFailed, errMsg)
		statusEvent.Final = true
		return q.Write(ctx, statusEvent)
	}

	parts := userMsg.Parts
	text := ""
	if len(parts) > 0 {
		if tp, ok := parts[0].(a2a.TextPart); ok {
			text = tp.Text
		}
	}

	if metadata["test_key"] == "full_message" {
		if len(parts) != 4 {
			return e.fail(ctx, q, reqCtx, fmt.Sprintf("Expected 4 parts, got %d", len(parts)))
		}
		if text != "stream" {
			return e.fail(ctx, q, reqCtx, fmt.Sprintf("Expected 'stream', got %s", text))
		}
		// Validate URI part
		if fp, ok := parts[1].(a2a.FilePart); !ok {
			return e.fail(ctx, q, reqCtx, "Part 1 is not FilePart")
		} else if fu, ok := fp.File.(a2a.FileURI); !ok || fu.URI != "https://example.com/file.txt" {
			return e.fail(ctx, q, reqCtx, "URI mismatch")
		}
		// Validate Bytes part
		if fp, ok := parts[2].(a2a.FilePart); !ok {
			return e.fail(ctx, q, reqCtx, "Part 2 is not FilePart")
		} else if fb, ok := fp.File.(a2a.FileBytes); !ok {
			return e.fail(ctx, q, reqCtx, fmt.Sprintf("Part 2 is not FileBytes: %T", fp.File))
		} else {
			received := fb.Bytes
			if received != "aGVsbG8=" {
				return e.fail(ctx, q, reqCtx, fmt.Sprintf("Bytes mismatch: got '%s'", received))
			}
		}
		// Validate Data part
		if dp, ok := parts[3].(a2a.DataPart); !ok || dp.Data["key"] != "value" {
			return e.fail(ctx, q, reqCtx, "Data mismatch")
		}
	}

	log.Printf("SERVER: request message text='%s'", text)

	if strings.Contains(text, "stream") {
		log.Printf("SERVER: waiting on stream event for task %s", taskID)
		ticker := time.NewTicker(100 * time.Millisecond)
		defer ticker.Stop()

	loop:
		for {
			select {
			case <-ctx.Done():
				return ctx.Err()
			case <-cancelChan:
				log.Printf("SERVER: stream event triggered for task %s", taskID)
				break loop
			case <-ticker.C:
				pingMsg := a2a.NewMessage(a2a.MessageRoleAgent, a2a.TextPart{Text: "ping"})
				q.Write(ctx, a2a.NewStatusUpdateEvent(reqCtx, a2a.TaskStateWorking, pingMsg))

				// Construct artifact update event
				artifact := &a2a.Artifact{
					ID:       a2a.NewArtifactID(),
					Name:     "test-artifact",
					Parts:    a2a.ContentParts{a2a.TextPart{Text: "artifact-chunk"}},
					Metadata: map[string]any{"artifact_key": "artifact_value"},
				}
				event := &a2a.TaskArtifactUpdateEvent{
					TaskID:    taskID,
					ContextID: contextID,
					Artifact:  artifact,
				}
				q.Write(ctx, event)
			}
		}
	}

	doneMsg := a2a.NewMessage(a2a.MessageRoleAgent, a2a.TextPart{Text: "done"})
	doneMsg.Metadata = map[string]any{"response_key": "response_value"}

	log.Printf("SERVER: execute finished for task %s", taskID)
	statusEvent := a2a.NewStatusUpdateEvent(reqCtx, a2a.TaskStateCompleted, doneMsg)
	statusEvent.Final = true
	return q.Write(ctx, statusEvent)
}

func (e *mockAgentExecutor) Cancel(ctx context.Context, reqCtx *a2asrv.RequestContext, q eventqueue.Queue) error {
	taskID := reqCtx.TaskID
	log.Printf("SERVER: cancel called for task %s", taskID)

	e.mu.Lock()
	ch, ok := e.cancelChans[taskID]
	e.mu.Unlock()

	if ok {
		close(ch)
	}

	statusEvent := a2a.NewStatusUpdateEvent(reqCtx, a2a.TaskStateCanceled, nil)
	statusEvent.Final = true
	return q.Write(ctx, statusEvent)
}

func (e *mockAgentExecutor) fail(ctx context.Context, q eventqueue.Queue, info a2a.TaskInfoProvider, reason string) error {
	log.Printf("SERVER FAIL: %s", reason)
	msg := a2a.NewMessage(a2a.MessageRoleAgent, a2a.TextPart{Text: reason})
	statusEvent := a2a.NewStatusUpdateEvent(info, a2a.TaskStateFailed, msg)
	statusEvent.Final = true
	return q.Write(ctx, statusEvent)
}

// historyFallbackHandler ensures history is returned to Python clients
type historyFallbackHandler struct {
	a2asrv.RequestHandler
}

func (h *historyFallbackHandler) OnGetTask(ctx context.Context, query *a2a.TaskQueryParams) (*a2a.Task, error) {
	if query.HistoryLength == nil || *query.HistoryLength <= 0 {
		hLength := 100
		query.HistoryLength = &hLength
	}
	return h.RequestHandler.OnGetTask(ctx, query)
}

func (h *historyFallbackHandler) OnGetExtendedAgentCard(ctx context.Context) (*a2a.AgentCard, error) {
	return h.RequestHandler.OnGetExtendedAgentCard(ctx)
}

func main() {
	httpPort := flag.Int("http-port", 0, "HTTP port")
	grpcPort := flag.Int("grpc-port", 0, "gRPC port")
	flag.Parse()

	if *httpPort == 0 || *grpcPort == 0 {
		log.Fatal("Missing --http-port or --grpc-port")
	}

	agentCard := &a2a.AgentCard{
		Name:               "Server 0.3 (Go)",
		Description:        "Go Server running on a2a v0.3.0",
		Version:            "1.0.0",
		URL:                fmt.Sprintf("http://127.0.0.1:%d/jsonrpc/", *httpPort),
		PreferredTransport: a2a.TransportProtocolJSONRPC,
		Capabilities:       a2a.AgentCapabilities{Streaming: true, PushNotifications: true},
		DefaultInputModes:  []string{"text/plain"},
		DefaultOutputModes: []string{"text/plain"},
		Skills:             make([]a2a.AgentSkill, 0),
		AdditionalInterfaces: []a2a.AgentInterface{
			{
				Transport: a2a.TransportProtocolGRPC,
				URL:       fmt.Sprintf("127.0.0.1:%d", *grpcPort),
			},
			{
				Transport: a2a.TransportProtocolHTTPJSON,
				URL:       fmt.Sprintf("http://127.0.0.1:%d/rest/", *httpPort),
			},
		},
	}

	executor := newMockAgentExecutor()
	innerHandler := a2asrv.NewHandler(executor, a2asrv.WithExtendedAgentCard(agentCard))
	handler := &historyFallbackHandler{innerHandler}

	// Custom Agent Card Handler to ensure supportsAuthenticatedExtendedCard: false is present in JSON
	cardJSON, _ := json.Marshal(map[string]any{
		"name":               agentCard.Name,
		"description":        agentCard.Description,
		"version":            agentCard.Version,
		"url":                agentCard.URL,
		"preferredTransport": agentCard.PreferredTransport,
		"capabilities":       agentCard.Capabilities,
		"defaultInputModes":  agentCard.DefaultInputModes,
		"defaultOutputModes": agentCard.DefaultOutputModes,
		"skills":             agentCard.Skills,
		"additionalInterfaces": agentCard.AdditionalInterfaces,
		"supportsAuthenticatedExtendedCard": false,
	})

	mux := http.NewServeMux()
	mux.Handle("/jsonrpc/", a2asrv.NewJSONRPCHandler(handler))
	
	cardHandler := http.HandlerFunc(func(w http.ResponseWriter, r *http.Request) {
		w.Header().Set("Content-Type", "application/json")
		w.Write(cardJSON)
	})
	mux.Handle("/jsonrpc/.well-known/agent-card.json", cardHandler)
	mux.Handle("/.well-known/agent-card.json", cardHandler)

	httpServer := &http.Server{
		Addr:    fmt.Sprintf("127.0.0.1:%d", *httpPort),
		Handler: mux,
	}

	go func() {
		log.Printf("SERVER: Starting JSON-RPC server on 127.0.0.1:%d", *httpPort)
		if err := httpServer.ListenAndServe(); err != nil && err != http.ErrServerClosed {
			log.Fatalf("HTTP server failed: %v", err)
		}
	}()

	grpcL, err := net.Listen("tcp", fmt.Sprintf("127.0.0.1:%d", *grpcPort))
	if err != nil {
		log.Fatalf("gRPC listen failed: %v", err)
	}

	grpcServer := grpc.NewServer()
	a2agrpc.NewHandler(handler).RegisterWith(grpcServer)

	go func() {
		log.Printf("SERVER: Starting gRPC server on 127.0.0.1:%d", *grpcPort)
		fmt.Println("SERVER READY") // Signal to test runner
		if err := grpcServer.Serve(grpcL); err != nil {
			log.Fatalf("gRPC server failed: %v", err)
		}
	}()

	select {}
}
