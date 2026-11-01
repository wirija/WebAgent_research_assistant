import argparse
import asyncio
import json
import os
import sys
from pathlib import Path

from dotenv import load_dotenv


class Parser(argparse.ArgumentParser):
    def error(self, message):
        self.print_usage(sys.stderr)
        self.exit(64, f'{self.prog}: error: {message}\n')


def main(argv=None):
    parser = Parser(description='Standalone Module 2: company understanding')
    parser.add_argument('--env-file', type=Path, help='Explicit environment file; default is this module’s .env')
    commands = parser.add_subparsers(dest='command', required=True)
    serve = commands.add_parser('serve', help='Run the independent local GUI/API')
    serve.add_argument('--port', type=int, default=8766)
    serve.add_argument('--output-dir', type=Path)
    run = commands.add_parser('run', help='Process a catalogue folder without any GUI or collector server')
    run.add_argument('--source', required=True, type=Path)
    run.add_argument('--financial-year-end', required=True)
    run.add_argument('--cutoff', required=True)
    run.add_argument('--output-dir', type=Path, default=Path(__file__).resolve().parent.parent / 'data')
    run.add_argument('--model', default=None)
    demo = commands.add_parser('demo', help='Exercise the workflow with synthetic evidence and no API calls')
    demo.add_argument('--output-dir', type=Path, default=Path(__file__).resolve().parent.parent / 'data')
    args = parser.parse_args(argv)
    load_dotenv(args.env_file or Path(__file__).resolve().parent.parent / '.env')
    if args.command == 'serve':
        if args.output_dir:
            os.environ['UNDERSTANDING_OUTPUT_DIR'] = str(args.output_dir.resolve())
        import uvicorn
        uvicorn.run('company_understanding.server:app', host='127.0.0.1', port=args.port)
        return
    from .engine import Job, Understanding, safe_error
    from .models import Request
    from .sample import SampleClient, create_sample
    try:
        is_demo = args.command == 'demo'
        if is_demo:
            folder = create_sample(args.output_dir / '_sample_input')
            request = Request(source_folder=str(folder), financial_year_end='2026-09-30', information_cutoff='2026-11-01')
        else:
            request = Request(source_folder=str(args.source), financial_year_end=args.financial_year_end, information_cutoff=args.cutoff)
        key = os.getenv('OPENAI_API_KEY', '')
        if not is_demo and not key:
            parser.error('Configure OPENAI_API_KEY in this module’s .env or the process environment.')
        job = Job(request, args.output_dir, getattr(args, 'model', None) or os.getenv('OPENAI_MODEL', 'gpt-5-mini'), demo=is_demo)
        result = asyncio.run(Understanding(job, key, SampleClient() if is_demo else None).execute())
        print(json.dumps({'contract_version': result['contract_version'], 'job_id': result['id'], 'status': result['status'],
                          'output_folder': str(job.folder), 'manifest': str(job.folder / 'job.json')}, indent=2))
        raise SystemExit(0 if result['status'] == 'finished' else 2 if result['status'] == 'partial' else 1)
    except (ValueError, OSError) as exc:
        parser.error(safe_error(exc))
