"""Fix absolute imports in *_pb2_grpc.py files.

Example:
import a2a_pb2 as a2a__pb2
from . import a2a_pb2 as a2a__pb2
"""

import re
import sys

from pathlib import Path


def process_generated_code(src_folder: str = 'src/a2a/grpc') -> None:
    """Post processor for the generated code."""
    dir_path = Path(src_folder)
    print(dir_path)
    if not dir_path.is_dir():
        print('Source folder not found')
        sys.exit(1)

    grpc_pattern = '**/*_pb2_grpc.py'
    files = dir_path.glob(grpc_pattern)

    for file in files:
        print(f'Processing {file}')
        try:
            with file.open('r', encoding='utf-8') as f:
                src_content = f.read()

                # Change import a2a_pb2 as a2a__pb2
                import_pattern = r'^import (\w+_pb2) as (\w+__pb2)$'
                # to from . import a2a_pb2 as a2a__pb2
                replacement_pattern = r'from . import \1 as \2'

                fixed_src_content = re.sub(
                    import_pattern,
                    replacement_pattern,
                    src_content,
                    flags=re.MULTILINE,
                )

            if fixed_src_content != src_content:
                with file.open('w', encoding='utf-8') as f:
                    f.write(fixed_src_content)
                    print('Imports fixed')
            else:
                print('No changes needed')

        except Exception as e:  # noqa: BLE001
            print(f'Error processing file {file}: {e}')
            sys.exit(1)


if __name__ == '__main__':
    process_generated_code()
