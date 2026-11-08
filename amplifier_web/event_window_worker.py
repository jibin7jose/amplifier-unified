"""Disposable bounded Work-window process, sharing the existing transport."""
import argparse
from .history_projection_worker import run, MEMORY_LIMIT


class WindowModel:
    def __init__(self, cache_home):
        pass

    def project(self, inputs, *, check_budget):
        from .event_window import read_window, read_field, StaleWindow
        try:
            if inputs.get('operation') == 'window':
                page = read_window(inputs['session'], window_key=inputs['windowKey'],
                    cursor_key=bytes.fromhex(inputs['cursorKey']), before=inputs.get('before'),
                    check_budget=check_budget)
                return {'status': 'complete', 'page': page}
            if inputs.get('operation') == 'field':
                value = read_field(inputs['session'], inputs['reference'],
                    offset=inputs.get('offset', 0), limit=inputs.get('limit', 4096),
                    check_budget=check_budget)
                return {'status': 'complete', 'field': value}
            raise ValueError('Unknown Work window operation')
        except StaleWindow:
            return {'status': 'retry', 'reason': 'source_changed'}
        except (MemoryError, InterruptedError):
            raise
        except (OSError, ValueError, ImportError) as error:
            from .work_diagnostics import failure
            return {'status': 'retry', 'reason': 'worker_failed',
                    'diagnostic': failure(error)}


if __name__ == '__main__':
    parser = argparse.ArgumentParser()
    parser.add_argument('--spool', required=True)
    parser.add_argument('--cache-home', required=True)
    parser.add_argument('--memory-limit', type=int, default=MEMORY_LIMIT)
    args = parser.parse_args()
    run(args.spool, args.cache_home, model_factory=WindowModel,
        memory_limit=args.memory_limit, input_limit=64 * 1024, output_limit=4 * 1024 * 1024)
