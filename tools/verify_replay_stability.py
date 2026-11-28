"""Verify fixed scientific bytes across effective Python seeds and document cache modes."""

import argparse
from collections import Counter
from contextlib import ExitStack
from hashlib import sha256
import json
import os
from pathlib import Path
import resource
import sqlite3
import subprocess
import sys
from time import perf_counter
from unittest.mock import patch
from weakref import WeakSet


def deny_network(event, *args):
    if event.startswith('socket.'):
        raise RuntimeError('Network disabled in scientific replay harness')


def measure(options):
    sys.addaudithook(deny_network)
    sys.path.insert(0, str(options.code / 'src'))
    from au_ree_v3 import documents, fresh_observations
    from au_ree_v3.fresh_research import build_research

    original_pdf = documents.pdf_pages
    original_connect = sqlite3.connect
    original_methods = {name:getattr(documents.DocumentCache,name) for name in ('pdf','html','csv')}
    cache_objects = WeakSet()
    pdf_pages, sql = [], Counter()
    peak_cache = Counter()
    warm_cache = documents.DocumentCache()

    def traced_pdf(content, last_page):
        result = original_pdf(content, last_page)
        pdf_pages.append(len(result))
        return result

    def traced_connect(*args, **kwargs):
        connection = original_connect(*args, **kwargs)
        connection.set_trace_callback(lambda query: sql.update((query.split(None, 1)[0].upper(),)))
        return connection

    def count_cache():
        sizes = Counter()
        for cache in list(cache_objects):
            sizes['pdf_entries'] += len(cache._pdf)
            sizes['pdf_pages_retained'] += sum(map(len, cache._pdf.values()))
            sizes['html_entries'] += len(cache._html)
            sizes['csv_entries'] += len(cache._csv)
            sizes['ocr_entries'] += len(cache._ocr)
        for key,value in sizes.items():
            peak_cache[key] = max(peak_cache[key], value)

    def observe(method):
        def wrapped(cache, *args, **kwargs):
            cache_objects.add(cache)
            if options.mode == 'disabled':
                for name in ('_pdf','_full_pdf','_html','_csv','_ocr'):
                    getattr(cache,name).clear()
            result = original_methods[method](cache, *args, **kwargs)
            if options.mode == 'disabled':
                for name in ('_pdf','_full_pdf','_html','_csv','_ocr'):
                    getattr(cache,name).clear()
            count_cache()
            return result
        return wrapped

    def process(current):
        pdf_pages.clear()
        sql.clear()
        peak_cache.clear()
        start = perf_counter()
        initial_cpu = cpu_seconds()
        report = build_research(profile=options.profile, bindings=options.bindings, sources=options.manifest,
                                assets=options.assets, out=options.out / current, code_commit=options.code_commit)
        artifacts = report['scientific_artifacts']
        for relative, fingerprint in artifacts.items():
            observed = sha256((options.out / current / relative).read_bytes()).hexdigest()
            if observed != fingerprint:
                raise ValueError(f'Wrong source build receipt fingerprint for {relative}')
        return {'mode':current, 'seconds':perf_counter()-start, 'cpu_seconds':cpu_seconds()-initial_cpu,
                'peak_rss_kib':resource.getrusage(resource.RUSAGE_SELF).ru_maxrss,
                'pdf_opens':len(pdf_pages), 'decoded_pages':sum(pdf_pages), 'sqlite_queries':sum(sql.values()),
                'sqlite_operations':dict(sql), 'peak_cache':dict(peak_cache), 'artifacts':artifacts}

    with ExitStack() as patches:
        patches.enter_context(patch.object(documents,'pdf_pages',traced_pdf))
        patches.enter_context(patch.object(sqlite3,'connect',traced_connect))
        for method in original_methods:
            patches.enter_context(patch.object(documents.DocumentCache,method,observe(method)))
        if options.mode == 'warm':
            patches.enter_context(patch.object(fresh_observations,'DocumentCache',lambda: warm_cache))
            samples = [process('first_resource_decode'),process('reused_resource_document_cache')]
        else:
            samples = [process(options.mode)]
    return {'seed':os.environ['PYTHONHASHSEED'],'hash_sentinel':hash('au-ree-census-replay'), 'samples':samples}


def cpu_seconds():
    usages = (resource.getrusage(who) for who in (resource.RUSAGE_SELF,resource.RUSAGE_CHILDREN))
    return sum(usage.ru_utime+usage.ru_stime for usage in usages)


def run(options):
    options.out.mkdir(parents=True)
    results = []
    for seed,mode in ((1,'cold'),(991,'disabled'),(17,'warm')):
        args = [sys.executable, '-B', '-s', str(Path(__file__).resolve()), '--mode', mode,
                '--out', str(options.out / mode), '--code-commit', options.code_commit]
        for name in ('code','profile','bindings','manifest','assets'):
            args.extend((f'--{name}',str(getattr(options,name))))
        values = {key:value for key,value in os.environ.items() if key not in {'PYTHONPATH','PYTHONHOME','PYTHONSAFEPATH'}}
        values['PYTHONHASHSEED'] = str(seed)
        result = subprocess.check_output(args,cwd=options.code,env=values)
        results.append(json.loads(result))
    artifacts = results[0]['samples'][0]['artifacts']
    observed = len({row['hash_sentinel'] for row in results})
    match = all(sample['artifacts'] == artifacts for row in results for sample in row['samples'])
    if not match or observed != len(results) or len(artifacts) != 8:
        raise ValueError('Replay differed between effective Python hash seeds or document-cache modes')
    report = {'status':'PASS','code_commit':options.code_commit,'source_manifest_sha256':sha256(options.manifest.read_bytes()).hexdigest(),
              'profile_sha256':sha256(options.profile.read_bytes()).hexdigest(), 'bindings_sha256':sha256(options.bindings.read_bytes()).hexdigest(),
              'results':results,'scientific_artifacts':artifacts,'scope':('Python socket audit-hook denial, uncached views discarded after each read; warm '
                 'mode injects prior resource DocumentCache. SQLite and CAS rechecked every run; OS filesystem cache uncontrolled.')}
    destination = options.out/'seed_cache_equivalence.json'
    with destination.open('x',encoding='utf-8') as stream:
        json.dump(report,stream,indent=2); stream.write('\n')
    print(destination)


if __name__ == '__main__':
    parser = argparse.ArgumentParser(description=__doc__)
    for field in ('code','profile','bindings','manifest','assets','out'):
        parser.add_argument(f'--{field}',type=lambda text:Path(text).resolve(),required=True)
    parser.add_argument('--code-commit',required=True)
    parser.add_argument('--mode',choices=('cold','warm','disabled'))
    config = parser.parse_args()
    if config.mode:
        print(json.dumps(measure(config)))
    else:
        run(config)
