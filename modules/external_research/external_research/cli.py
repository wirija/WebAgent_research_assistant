import argparse
import asyncio
import json
import os
from pathlib import Path
from dotenv import load_dotenv


def main(argv=None):
    p=argparse.ArgumentParser(description='Standalone Module 3: external environment research')
    p.add_argument('--env-file',type=Path)
    commands=p.add_subparsers(dest='command',required=True)
    serve=commands.add_parser('serve');serve.add_argument('--port',type=int,default=8767)
    run=commands.add_parser('run')
    run.add_argument('--source',required=True);run.add_argument('--audit-period-start',required=True)
    run.add_argument('--audit-period-end',required=True);run.add_argument('--cutoff',required=True)
    run.add_argument('--model',default=None)
    demo=commands.add_parser('demo')
    for command in [serve,run,demo]:command.add_argument('--output-dir',type=Path,default=Path(__file__).resolve().parent.parent/'data')
    args=p.parse_args(argv)
    load_dotenv(args.env_file or Path(__file__).resolve().parent.parent/'.env')
    if args.command=='serve':
        os.environ['EXTERNAL_RESEARCH_OUTPUT_DIR']=str(args.output_dir.resolve())
        import uvicorn
        uvicorn.run('external_research.server:app',host='127.0.0.1',port=args.port);return
    from .engine import Job, Research, safe_error
    from .models import Request
    from .sample import SampleClient, SampleFetcher, create_sample
    try:
        is_demo=args.command=='demo'
        request=Request(source_folder=str(create_sample(args.output_dir/'_sample_input')) if is_demo else args.source,
            audit_period_start='2025-10-01' if is_demo else args.audit_period_start,
            audit_period_end='2026-09-30' if is_demo else args.audit_period_end,
            information_cutoff='2026-11-01' if is_demo else args.cutoff)
        key=os.getenv('OPENAI_API_KEY','')
        if not is_demo and not key:p.error('Configure OPENAI_API_KEY in the module environment.')
        job=Job(request,args.output_dir,getattr(args,'model',None) or os.getenv('OPENAI_MODEL','gpt-5-mini'),demo=is_demo)
        result=asyncio.run(Research(job,key,SampleClient() if is_demo else None,SampleFetcher() if is_demo else None).execute())
        print(json.dumps({k:result[k] for k in ['id','status','folder']},indent=2))
        raise SystemExit(0 if result['status']=='finished' else 2 if result['status']=='partial' else 1)
    except (ValueError,OSError) as exc:p.error(safe_error(exc))
