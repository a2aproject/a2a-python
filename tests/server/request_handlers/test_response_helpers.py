import unittest

from unittest.mock import patch

from a2a.server.request_handlers.response_helpers import (
    build_error_response,
    prepare_response_object,
)
from a2a.types import (
    A2AError,
    GetTaskResponse,
    GetTaskSuccessResponse,
    InvalidAgentResponseError,
    InvalidParamsError,
    JSONRPCError,
    JSONRPCErrorResponse,
    Task,
    TaskNotFoundError,
    TaskState,
    TaskStatus,
)


class TestResponseHelpers(unittest.TestCase):
    def test_build_error_response_with_a2a_error(self) -> None:
        request_id = 'req1'
        specific_error = TaskNotFoundError()
        a2a_error = A2AError(root=specific_error)  # Correctly wrap
        response_wrapper = build_error_response(
            request_id, a2a_error, GetTaskResponse
        )
        self.assertIsInstance(response_wrapper, GetTaskResponse)
        self.assertIsInstance(response_wrapper.root, JSONRPCErrorResponse)
        self.assertEqual(response_wrapper.root.id, request_id)
        self.assertEqual(
            response_wrapper.root.error, specific_error
        )  # build_error_response unwraps A2AError

    def test_build_error_response_with_jsonrpc_error(self) -> None:
        request_id = 123
        json_rpc_error = InvalidParamsError(
            message='Custom invalid params'
        )  # This is a specific error, not A2AError wrapped
        response_wrapper = build_error_response(
            request_id, json_rpc_error, GetTaskResponse
        )
        self.assertIsInstance(response_wrapper, GetTaskResponse)
        self.assertIsInstance(response_wrapper.root, JSONRPCErrorResponse)
        self.assertEqual(response_wrapper.root.id, request_id)
        self.assertEqual(
            response_wrapper.root.error, json_rpc_error
        )  # No .root access for json_rpc_error

    def test_build_error_response_with_a2a_wrapping_jsonrpc_error(self) -> None:
        request_id = 'req_wrap'
        specific_jsonrpc_error = InvalidParamsError(message='Detail error')
        a2a_error_wrapping = A2AError(
            root=specific_jsonrpc_error
        )  # Correctly wrap
        response_wrapper = build_error_response(
            request_id, a2a_error_wrapping, GetTaskResponse
        )
        self.assertIsInstance(response_wrapper, GetTaskResponse)
        self.assertIsInstance(response_wrapper.root, JSONRPCErrorResponse)
        self.assertEqual(response_wrapper.root.id, request_id)
        self.assertEqual(response_wrapper.root.error, specific_jsonrpc_error)

    def test_build_error_response_with_request_id_string(self) -> None:
        request_id = 'string_id_test'
        # Pass an A2AError-wrapped specific error for consistency with how build_error_response handles A2AError
        error = A2AError(root=TaskNotFoundError())
        response_wrapper = build_error_response(
            request_id, error, GetTaskResponse
        )
        self.assertIsInstance(response_wrapper.root, JSONRPCErrorResponse)
        self.assertEqual(response_wrapper.root.id, request_id)

    def test_build_error_response_with_request_id_int(self) -> None:
        request_id = 456
        error = A2AError(root=TaskNotFoundError())
        response_wrapper = build_error_response(
            request_id, error, GetTaskResponse
        )
        self.assertIsInstance(response_wrapper.root, JSONRPCErrorResponse)
        self.assertEqual(response_wrapper.root.id, request_id)

    def test_build_error_response_with_request_id_none(self) -> None:
        request_id = None
        error = A2AError(root=TaskNotFoundError())
        response_wrapper = build_error_response(
            request_id, error, GetTaskResponse
        )
        self.assertIsInstance(response_wrapper.root, JSONRPCErrorResponse)
        self.assertIsNone(response_wrapper.root.id)

    def _create_sample_task(
        self, task_id: str = 'task123', context_id: str = 'ctx456'
    ) -> Task:
        return Task(
            id=task_id,
            context_id=context_id,
            status=TaskStatus(state=TaskState.submitted),
            history=[],
        )

    def test_prepare_response_object_successful_response(self) -> None:
        request_id = 'req_success'
        task_result = self._create_sample_task()
        response_wrapper = prepare_response_object(
            request_id=request_id,
            response=task_result,
            success_response_types=(Task,),
            success_payload_type=GetTaskSuccessResponse,
            response_type=GetTaskResponse,
        )
        self.assertIsInstance(response_wrapper, GetTaskResponse)
        self.assertIsInstance(response_wrapper.root, GetTaskSuccessResponse)
        self.assertEqual(response_wrapper.root.id, request_id)
        self.assertEqual(response_wrapper.root.result, task_result)

    @patch('a2a.server.request_handlers.response_helpers.build_error_response')
    def test_prepare_response_object_with_a2a_error_instance(
        self, mock_build_error
    ) -> None:
        request_id = 'req_a2a_err'
        specific_error = TaskNotFoundError()
        a2a_error_instance = A2AError(
            root=specific_error
        )  # Correctly wrapped A2AError

        # This is what build_error_response (when called by prepare_response_object) will return
        mock_wrapped_error_response = GetTaskResponse(
            root=JSONRPCErrorResponse(
                id=request_id, error=specific_error, jsonrpc='2.0'
            )
        )
        mock_build_error.return_value = mock_wrapped_error_response

        response_wrapper = prepare_response_object(
            request_id=request_id,
            response=a2a_error_instance,  # Pass the A2AError instance
            success_response_types=(Task,),
            success_payload_type=GetTaskSuccessResponse,
            response_type=GetTaskResponse,
        )
        # prepare_response_object should identify A2AError and call build_error_response
        mock_build_error.assert_called_once_with(
            request_id, a2a_error_instance, GetTaskResponse
        )
        self.assertEqual(response_wrapper, mock_wrapped_error_response)

    @patch('a2a.server.request_handlers.response_helpers.build_error_response')
    def test_prepare_response_object_with_jsonrpcerror_base_instance(
        self, mock_build_error
    ) -> None:
        request_id = 789
        # Use the base JSONRPCError class instance
        json_rpc_base_error = JSONRPCError(
            code=-32000, message='Generic JSONRPC error'
        )

        mock_wrapped_error_response = GetTaskResponse(
            root=JSONRPCErrorResponse(
                id=request_id, error=json_rpc_base_error, jsonrpc='2.0'
            )
        )
        mock_build_error.return_value = mock_wrapped_error_response

        response_wrapper = prepare_response_object(
            request_id=request_id,
            response=json_rpc_base_error,  # Pass the JSONRPCError instance
            success_response_types=(Task,),
            success_payload_type=GetTaskSuccessResponse,
            response_type=GetTaskResponse,
        )
        # prepare_response_object should identify JSONRPCError and call build_error_response
        mock_build_error.assert_called_once_with(
            request_id, json_rpc_base_error, GetTaskResponse
        )
        self.assertEqual(response_wrapper, mock_wrapped_error_response)

    @patch('a2a.server.request_handlers.response_helpers.build_error_response')
    def test_prepare_response_object_specific_error_model_as_unexpected(
        self, mock_build_error
    ) -> None:
        request_id = 'req_specific_unexpected'
        # Pass a specific error model (like TaskNotFoundError) directly, NOT wrapped in A2AError
        # This should be treated as an "unexpected" type by prepare_response_object's current logic
        specific_error_direct = TaskNotFoundError()

        # This is the InvalidAgentResponseError that prepare_response_object will generate
        generated_error_wrapper = A2AError(
            root=InvalidAgentResponseError(
                message='Agent returned invalid type response for this method'
            )
        )

        # This is what build_error_response will be called with (the generated error)
        # And this is what it will return (the generated error, wrapped in GetTaskResponse)
        mock_final_wrapped_response = GetTaskResponse(
            root=JSONRPCErrorResponse(
                id=request_id, error=generated_error_wrapper.root, jsonrpc='2.0'
            )
        )
        mock_build_error.return_value = mock_final_wrapped_response

        response_wrapper = prepare_response_object(
            request_id=request_id,
            response=specific_error_direct,  # Pass TaskNotFoundError() directly
            success_response_types=(Task,),
            success_payload_type=GetTaskSuccessResponse,
            response_type=GetTaskResponse,
        )

        self.assertEqual(mock_build_error.call_count, 1)
        args, _ = mock_build_error.call_args
        self.assertEqual(args[0], request_id)
        # Check that the error passed to build_error_response is the generated A2AError(InvalidAgentResponseError)
        self.assertIsInstance(args[1], A2AError)
        self.assertIsInstance(args[1].root, InvalidAgentResponseError)
        self.assertEqual(args[2], GetTaskResponse)
        self.assertEqual(response_wrapper, mock_final_wrapped_response)

    def test_prepare_response_object_with_request_id_string(self) -> None:
        request_id = 'string_id_prep'
        task_result = self._create_sample_task()
        response_wrapper = prepare_response_object(
            request_id=request_id,
            response=task_result,
            success_response_types=(Task,),
            success_payload_type=GetTaskSuccessResponse,
            response_type=GetTaskResponse,
        )
        self.assertIsInstance(response_wrapper.root, GetTaskSuccessResponse)
        self.assertEqual(response_wrapper.root.id, request_id)

    def test_prepare_response_object_with_request_id_int(self) -> None:
        request_id = 101112
        task_result = self._create_sample_task()
        response_wrapper = prepare_response_object(
            request_id=request_id,
            response=task_result,
            success_response_types=(Task,),
            success_payload_type=GetTaskSuccessResponse,
            response_type=GetTaskResponse,
        )
        self.assertIsInstance(response_wrapper.root, GetTaskSuccessResponse)
        self.assertEqual(response_wrapper.root.id, request_id)

    def test_prepare_response_object_with_request_id_none(self) -> None:
        request_id = None
        task_result = self._create_sample_task()
        response_wrapper = prepare_response_object(
            request_id=request_id,
            response=task_result,
            success_response_types=(Task,),
            success_payload_type=GetTaskSuccessResponse,
            response_type=GetTaskResponse,
        )
        self.assertIsInstance(response_wrapper.root, GetTaskSuccessResponse)
        self.assertIsNone(response_wrapper.root.id)


if __name__ == '__main__':
    unittest.main()
