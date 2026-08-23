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

const expectScript = `
set timeout -1
set password $env(DEPLOY_PASSWORD)

spawn ssh -T -o StrictHostKeyChecking=accept-new ${host} bash -s

expect {
  "*yes/no*" {
    send -- "yes\\r"
    exp_continue
  }
  "*assword:*" {
    send -- "$password\\r"
  }
}

send -- "set -e\\r"
send -- "cd plant-based\\r"
send -- "git pull\\r"
send -- ".venv/bin/pip install -r hub/requirements.txt\\r"
send -- "sudo -p 'DEPLOY_SUDO_PASSWORD:' systemctl restart plant-hub\\r"
send -- "sudo -p 'DEPLOY_SUDO_PASSWORD:' systemctl status plant-hub --no-pager\\r"

expect {
  "DEPLOY_SUDO_PASSWORD:" {
    send -- "$password\\r"
    exp_continue
  }
  eof
}
`;

const deploy = spawn(
  '/usr/bin/expect',
  ['-f', '-'],
  {
    env: { ...process.env, DEPLOY_PASSWORD: password },
    stdio: ['pipe', 'inherit', 'inherit'],
  },
);

deploy.stdin.end(expectScript);

deploy.on('error', (error) => {
  if (error.code === 'ENOENT') {
    console.error('Comando expect non trovato. Su macOS è normalmente disponibile in /usr/bin/expect.');
    process.exitCode = 1;
    return;
  }

  console.error(`Errore durante il deploy SSH: ${error.message}`);
  process.exitCode = 1;
});

deploy.on('close', (code, signal) => {
  if (signal) {
    console.error(`La connessione SSH è terminata dal segnale ${signal}.`);
    process.exitCode = 1;
    return;
  }

  process.exitCode = code ?? 1;
});