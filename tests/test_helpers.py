import asyncio

from unittest.mock import ANY, patch

import pytest

from tests.helpers import actively_print_and_yield_async_gen


async def async_gen_success():
    yield 'a'
    yield 'b'
    yield 'c'


async def async_gen_slow():
    yield 1
    await asyncio.sleep(0.5)
    yield 2


async def async_gen_fail():
    yield 1
    raise ValueError('gen fail')


@pytest.mark.asyncio
async def test_actively_print_and_yield_success():
    items = []
    gen = async_gen_success()
    expected_name = str(gen)
    with patch('tests.helpers.logger.info') as mocked_print:
        items = [item async for item in actively_print_and_yield_async_gen(gen)]

    assert items == ['a', 'b', 'c']
    assert mocked_print.call_count == 4
    mocked_print.assert_any_call('[%s] Generated: %s', expected_name, 'a')
    mocked_print.assert_any_call('[%s] Generated: %s', expected_name, 'b')
    mocked_print.assert_any_call('[%s] Generated: %s', expected_name, 'c')
    mocked_print.assert_any_call('[%s] Ended', expected_name)


@pytest.mark.asyncio
async def test_actively_print_and_yield_with_name():
    with patch('tests.helpers.logger.info') as mocked_print:
        items = [
            item
            async for item in actively_print_and_yield_async_gen(
                async_gen_success(), name='TEST_GEN'
            )
        ]

    assert items == ['a', 'b', 'c']
    assert mocked_print.call_count == 4
    mocked_print.assert_any_call('[%s] Generated: %s', 'TEST_GEN', 'a')
    mocked_print.assert_any_call('[%s] Generated: %s', 'TEST_GEN', 'b')
    mocked_print.assert_any_call('[%s] Generated: %s', 'TEST_GEN', 'c')
    mocked_print.assert_any_call('[%s] Ended', 'TEST_GEN')


@pytest.mark.asyncio
async def test_actively_print_even_if_not_consumed():
    gen = async_gen_slow()
    expected_name = str(gen)
    with patch('tests.helpers.logger.info') as mocked_print:
        # Start the generator
        it = actively_print_and_yield_async_gen(gen)

        # We don't iterate 'it' immediately.
        # But wait enough time for the background task to progress.
        # It should print "1" immediately, then wait 0.5s and print "2".
        await asyncio.sleep(0.7)

        # By now it should have printed both.
        assert mocked_print.call_count == 3
        mocked_print.assert_any_call('[%s] Generated: %s', expected_name, 1)
        mocked_print.assert_any_call('[%s] Generated: %s', expected_name, 2)
        mocked_print.assert_any_call('[%s] Ended', expected_name)

        # Now consume it - items should still be in the queue.
        items = [item async for item in it]
        assert items == [1, 2]


@pytest.mark.asyncio
async def test_actively_print_fail():
    gen = async_gen_fail()
    expected_name = str(gen)
    with patch('tests.helpers.logger.info') as mocked_print:
        with pytest.raises(ValueError, match='gen fail'):
            async for _ in actively_print_and_yield_async_gen(gen):
                pass

        assert mocked_print.call_count == 2
        mocked_print.assert_any_call('[%s] Generated: %s', expected_name, 1)
        mocked_print.assert_any_call(
            '[%s] Raised exception: %s', expected_name, ANY
        )


@pytest.mark.asyncio
async def test_cleanup_on_early_stop():
    # If the consumer stops early, the background task should be cancelled.
    with patch('tests.helpers.logger.info'):
        it = actively_print_and_yield_async_gen(async_gen_slow())
        async for _ in it:
            break
        # The finally block in _producer should have run.
        # Wait a bit to ensure the task had time to be cancelled.
        await asyncio.sleep(0.1)


@pytest.mark.asyncio
async def test_cleanup_on_aclose():
    # Force kill via aclose()
    with patch('tests.helpers.logger.info'):
        it = actively_print_and_yield_async_gen(async_gen_slow())
        # First item is already printed because it started immediately.
        await it.aclose()
        # Task should be cancelled and cleaned up.
        await asyncio.sleep(0.1)


@pytest.mark.asyncio
async def test_generator_exception_propagation():
    async def failing_gen():
        yield 1
        raise RuntimeError('Interrupted')

    it = actively_print_and_yield_async_gen(failing_gen())

    with (
        patch('tests.helpers.logger.info'),
        pytest.raises(RuntimeError, match='Interrupted'),
    ):
        async for item in it:
            assert item == 1


@pytest.mark.asyncio
async def test_task_cancelled_externally():
    cancel_flag = []

    async def track_cancel_gen():
        try:
            yield 1
            await asyncio.sleep(10)
            yield 2
        except asyncio.CancelledError:
            cancel_flag.append(True)
            raise

    it = actively_print_and_yield_async_gen(track_cancel_gen())

    async for item in it:
        assert item == 1
        break

    await it.aclose()

    assert cancel_flag == [True]
