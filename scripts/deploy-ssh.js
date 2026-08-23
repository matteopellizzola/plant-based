const { spawn } = require('node:child_process');

const password = process.argv.slice(2).filter((argument) => argument !== '--').join(' ');
const host = 'pellipi@192.168.1.10';

if (!password) {
  console.error('Uso: npm run deploy-ssh -- <password>');
  process.exit(1);
}

if (password.includes('\n') || password.includes('\r')) {
  console.error('La password non può contenere ritorni a capo.');
  process.exit(1);
}

const remoteScript = `
set -e
IFS= read -r sudo_password
cd plant-based
git pull
.venv/bin/pip install -r hub/requirements.txt
printf '%s\\n' "$sudo_password" | sudo -S -p '' systemctl restart plant-hub
printf '%s\\n' "$sudo_password" | sudo -S -p '' systemctl status plant-hub --no-pager
`;

const ssh = spawn(
  'sshpass',
  ['-e', 'ssh', '-T', '-o', 'StrictHostKeyChecking=accept-new', host, 'bash', '-s'],
  {
    env: { ...process.env, SSHPASS: password },
    stdio: ['pipe', 'inherit', 'inherit'],
  },
);

ssh.stdin.end(`${password}\n${remoteScript}`);

ssh.on('error', (error) => {
  if (error.code === 'ENOENT') {
    console.error('Comando sshpass non trovato. Installa sshpass con: brew install hudochenkov/sshpass/sshpass');
    process.exitCode = 1;
    return;
  }

  console.error(`Errore durante il deploy SSH: ${error.message}`);
  process.exitCode = 1;
});

ssh.on('close', (code, signal) => {
  if (signal) {
    console.error(`La connessione SSH è terminata dal segnale ${signal}.`);
    process.exitCode = 1;
    return;
  }

  process.exitCode = code ?? 1;
});