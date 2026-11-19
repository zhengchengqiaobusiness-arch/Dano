"""Assemble the host-side deployment bundle from verified release inputs."""
import argparse
import json
from pathlib import Path
import shutil
import sys

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / 'deploy/production'))
from bundle import digest, validate_images


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--output', required=True, type=Path)
    parser.add_argument('--images', required=True, type=Path)
    parser.add_argument('--archive', type=Path, help='Docker save archive containing both manifest images')
    args = parser.parse_args()
    metadata = json.loads(args.images.read_text())
    validate_images(metadata)
    metadata['version'] = json.loads((ROOT / 'package.json').read_text())['version']
    args.output.mkdir()
    assets = {
        'deploy.sh': 'deploy/bundle/deploy.sh',
        'instance.example.json': 'deploy/bundle/instance.example.json',
        'dano.env.example': 'deploy/bundle/dano.env.example',
        'README.md': 'deploy/bundle/README.md',
        'bundle.py': 'deploy/production/bundle.py',
        'release.py': 'deploy/production/release.py',
        'docker-compose.yml': 'docker-compose.yml',
        'compose/standalone.yml': 'deploy/bundle/standalone.yml',
        'compose/http.yml': 'deploy/compose/http.yml',
        'compose/https.yml': 'deploy/compose/https.yml',
        'compose-filter.mjs': '.agents/skills/dano-production-deploy/scripts/summarize-compose-config.mjs',
        'smoke.mjs': 'scripts/smoke-dano-deploy.mjs',
    }
    for directory in ('nginx', 'help'):
        for source in (ROOT / 'deploy' / directory).rglob('*'):
            if source.is_file():
                assets[str(source.relative_to(ROOT / 'deploy'))] = str(source.relative_to(ROOT))
    for name, source in assets.items():
        target = args.output / name
        target.parent.mkdir(parents=True, exist_ok=True)
        shutil.copy2(ROOT / source, target)
    metadata['files'] = {name: digest(args.output / name) for name in sorted(assets)}
    if args.archive:
        target = args.output / 'images.tar'
        shutil.copyfile(args.archive, target)
        metadata['archiveSha256'] = digest(target)
    (args.output / 'manifest.json').write_text(json.dumps(metadata, indent=2) + '\n')


if __name__ == '__main__':
    main()
