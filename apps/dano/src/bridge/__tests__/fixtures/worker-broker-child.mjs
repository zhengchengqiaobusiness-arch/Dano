// Real IPC lifecycle fixture. It deliberately does not claim UID isolation.
import { serveWorkerBroker } from '../../worker-broker.ts';
const mode = process.argv[2];
if (mode === 'silent') {
  process.on('message', () => {});
} else {
  serveWorkerBroker({ workspace: '/fixture', assertIsolated: async () => {}, close() {},
    async execute(_name, parameters, signal, update) {
      if (parameters.crash) process.exit(23);
      update?.({ progress: true });
      if (parameters.wait) await new Promise((resolve, reject) => {
        const timer = setTimeout(resolve, parameters.wait);
        signal.addEventListener('abort', () => { clearTimeout(timer); reject(new Error('cancelled')); }, { once: true });
      });
      return { value: parameters.value ?? 'done' };
    },
  }, {
    get connected() { return process.connected; },
    on: process.on.bind(process), off: process.off.bind(process),
    send: (message, callback) => process.send(message, callback),
    disconnect: () => process.disconnect(),
  }, { maxConcurrentOperations: 4, maxResultBytes: 4096 });
}
