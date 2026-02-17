import unittest

from google.protobuf.json_format import MessageToDict

from a2a.server.request_handlers.response_helpers import (
    build_error_response,
    prepare_response_object,
)
from a2a.server.jsonrpc_models import JSONRPCError
from a2a.types import (
    InvalidParamsError,
    TaskNotFoundError,
)
from a2a.types.a2a_pb2 import (
    Task,
    TaskState,
    TaskStatus,
)


class TestResponseHelpers(unittest.TestCase):
    def test_build_error_response_with_a2a_error(self) -> None:
        request_id = 'req1'
        specific_error = TaskNotFoundError()
        response = build_error_response(request_id, specific_error)

        # Response is now a dict with JSON-RPC 2.0 structure
        self.assertIsInstance(response, dict)
        self.assertEqual(response.get('jsonrpc'), '2.0')
        self.assertEqual(response.get('id'), request_id)
        self.assertIn('error', response)
        self.assertEqual(response['error']['code'], -32001)
        self.assertEqual(response['error']['message'], specific_error.message)

    def test_build_error_response_with_jsonrpc_error(self) -> None:
        request_id = 123
        json_rpc_error = InvalidParamsError(message='Custom invalid params')
        response = build_error_response(request_id, json_rpc_error)

        self.assertIsInstance(response, dict)
        self.assertEqual(response.get('jsonrpc'), '2.0')
        self.assertEqual(response.get('id'), request_id)
        self.assertIn('error', response)
        self.assertEqual(response['error']['code'], -32602)
        self.assertEqual(response['error']['message'], json_rpc_error.message)

    def test_build_error_response_with_invalid_params_error(self) -> None:
        request_id = 'req_wrap'
        specific_jsonrpc_error = InvalidParamsError(message='Detail error')
        response = build_error_response(request_id, specific_jsonrpc_error)

        self.assertIsInstance(response, dict)
        self.assertEqual(response.get('jsonrpc'), '2.0')
        self.assertEqual(response.get('id'), request_id)
        self.assertIn('error', response)
        self.assertEqual(response['error']['code'], -32602)
        self.assertEqual(
            response['error']['message'], specific_jsonrpc_error.message
        )

    def test_build_error_response_with_request_id_string(self) -> None:
        request_id = 'string_id_test'
        error = TaskNotFoundError()
        response = build_error_response(request_id, error)

        self.assertIsInstance(response, dict)
        self.assertIn('error', response)
        self.assertEqual(response.get('id'), request_id)

    def test_build_error_response_with_request_id_int(self) -> None:
        request_id = 456
        error = TaskNotFoundError()
        response = build_error_response(request_id, error)

        self.assertIsInstance(response, dict)
        self.assertIn('error', response)
        self.assertEqual(response.get('id'), request_id)

    def test_build_error_response_with_request_id_none(self) -> None:
        request_id = None
        error = TaskNotFoundError()
        response = build_error_response(request_id, error)

        self.assertIsInstance(response, dict)
        self.assertIn('error', response)
        self.assertIsNone(response.get('id'))

    def _create_sample_task(
        self, task_id: str = 'task123', context_id: str = 'ctx456'
    ) -> Task:
        return Task(
            id=task_id,
            context_id=context_id,
            status=TaskStatus(state=TaskState.TASK_STATE_SUBMITTED),
            history=[],
        )

    def test_prepare_response_object_with_proto_message(self) -> None:
        request_id = 'req_success'
        task_result = self._create_sample_task()
        response = prepare_response_object(
            request_id=request_id,
            response=task_result,
            success_response_types=(Task,),
        )

        # Response is now a dict with JSON-RPC 2.0 structure
        self.assertIsInstance(response, dict)
        self.assertEqual(response.get('jsonrpc'), '2.0')
        self.assertEqual(response.get('id'), request_id)
        self.assertIn('result', response)
        # Result is the proto message converted to dict
        expected_result = MessageToDict(
            task_result, preserving_proto_field_name=False
        )
        self.assertEqual(response['result'], expected_result)

    def test_prepare_response_object_with_error(self) -> None:
        request_id = 'req_error'
        error = TaskNotFoundError()
        response = prepare_response_object(
            request_id=request_id,
            response=error,
            success_response_types=(Task,),
        )

        self.assertIsInstance(response, dict)
        self.assertEqual(response.get('jsonrpc'), '2.0')
        self.assertEqual(response.get('id'), request_id)
        self.assertIn('error', response)
        self.assertEqual(response['error']['code'], -32001)

    def test_prepare_response_object_with_invalid_response(self) -> None:
        request_id = 'req_invalid'
        invalid_response = object()
        response = prepare_response_object(
            request_id=request_id,
            response=invalid_response,  # type: ignore
            success_response_types=(Task,),
        )

        # Should return an InvalidAgentResponseError
        self.assertIsInstance(response, dict)
        self.assertIn('error', response)
        # Check that it's an InvalidAgentResponseError (code -32006)
        self.assertEqual(response['error']['code'], -32006)


if __name__ == '__main__':
    unittest.main()
