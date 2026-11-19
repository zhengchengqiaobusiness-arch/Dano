"""Synthetic Docker service used by the deployment command integration tests."""
import json
from pathlib import Path
import sys

root = Path(sys.argv[0]).resolve().parent
data_path = root / 'engine.json'
state = json.loads(data_path.read_text())
args = sys.argv[1:]


def emit(value):
    print(json.dumps(value))


def environment(path):
    return {key: value.strip("'") for key, value in
            (line.split('=', 1) for line in Path(path).read_text().splitlines()
             if line and not line.startswith('#'))}


def compose():
    command = args[args.index('--env-file') + 2:]
    env = environment(args[args.index('--env-file') + 1])
    if command[0] == 'config':
        emit({'services': {'app': {'environment': env, 'volumes': [
            {'target': '/opt/dano/runtime-data', 'source': env['DANO_RUNTIME_DIR']}]},
            'nginx': {'image': env['DANO_NGINX_IMAGE'], 'ports': [{'published': '38333'}]}}})
    elif command[0] == 'up':
        files = [str(Path(args[i + 1]).resolve()) for i, value in enumerate(args) if value == '-f']
        state['current'] = env['DANO_IMAGE']
        previous = {container['Config']['Labels']['com.docker.compose.service']: container
                    for container in state['containers']}
        state['generation'] = state.get('generation', 0) + 1
        services = ['app'] if '--no-deps' in command else ['app', 'nginx']
        changed = {
            service: {'Id': service + '-' + str(state['generation']), 'Image': env.get('DANO_NGINX_IMAGE') if service == 'nginx' else state['current'],
             'RestartCount': 0, 'State': {'Health': {'Status': 'healthy'}},
             'Config': {'Labels': {'com.docker.compose.service': service,
                                   'com.docker.compose.project.working_dir': str(Path.cwd()),
                                   'com.docker.compose.project.config_files': ','.join(files)}},
             'NetworkSettings': {}, 'Mounts': [{'Destination': '/opt/dano/runtime-data',
                 'Source': env['DANO_RUNTIME_DIR'], 'RW': True}]}
            for service in services}
        state['containers'] = list(dict(previous, **changed).values())
        data_path.write_text(json.dumps(state))
    elif command[0] not in ('run', 'exec'):
        sys.exit(2)


if args[:3] == ['compose', 'version', '--short']:
    print('2.32.4')
elif args[:1] == ['compose']:
    compose()
elif args[:1] == ['ps']:
    print('\n'.join(c['Id'] for c in state['containers']))
elif args[:1] == ['inspect']:
    emit([c for c in state['containers'] if c['Id'] in args[1:]])
elif args[:2] == ['image', 'inspect']:
    image_id = state['refs'].get(args[2], args[2])
    image = state['images'][image_id]
    emit([{'Id': image_id, 'Os': 'linux', 'Architecture': 'amd64',
           'Config': {'Labels': {'org.opencontainers.image.revision': image['revision']}}}])
elif args[:2] in (['image', 'pull'], ['image', 'load']):
    pass
elif args[:1] == ['run']:
    if args[-1] == '/smoke.mjs':
        sys.exit(int(state.get('failImage') == state['current']))
    if args[-2] == '-e':
        emit({'version': state['images'][args[-3]]['version'], 'assets': ['entry.js']})
elif args[:1] != ['info'] and args[:2] not in (['volume', 'ls'], ['network', 'ls']):
    sys.exit(2)
