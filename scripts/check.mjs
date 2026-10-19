// One offline test entrypoint, after the documented dependency/browser installation.
import { spawnSync } from 'node:child_process';
import { existsSync } from 'node:fs';
import path from 'node:path';

process.env.PLAYWRIGHT_BROWSERS_PATH ||= '0';

const python = process.env.SANYOJAN_TEST_PYTHON ||
  path.join('.venv', process.platform === 'win32' ? 'Scripts/python.exe' : 'bin/python');
if (!existsSync(python)) {
  console.error('Project Python missing. Run uv sync --frozen first.');
  process.exit(1);
}
function run(command, args) {
  const result = spawnSync(command, args, { stdio: 'inherit', shell: false });
  if (result.error) console.error(result.error.message);
  if (result.status !== 0) process.exit(result.status || 1);
}
// npm_execpath avoids Windows .cmd shell interpolation.
run(process.execPath, [process.env.npm_execpath, 'run', 'build']);
run(python, ['-m', 'pytest', '-q']);
run(process.execPath, [process.env.npm_execpath, 'run', 'test:frontend']);
