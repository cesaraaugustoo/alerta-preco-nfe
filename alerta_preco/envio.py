"""O e-mail, enviado por SSH pelo servidor que guarda a senha, e o aviso na tela quando ele não sai.

A senha do e-mail não sai do servidor: um programa curto roda lá, lê o arquivo de segredos e recebe o conteúdo
por entrada padrão. Por isso nenhuma mensagem de erro traz o comando nem o conteúdo, só o motivo.
"""

import base64
import contextlib
import json
import os
import subprocess
import sys

from .modelos import Relatorio

TEMPO_LIMITE = 120
TAMANHO_DO_MOTIVO = 160
TAMANHO_DO_AVISO = 250

# Roda no servidor. Depois de enviar, imprime só ASCII: um erro de acento na resposta faria um e-mail já enviado
# parecer falha, e a próxima tentativa o repetiria.
PROGRAMA_REMOTO = """
import json, smtplib, sys
from email.message import EmailMessage
d = json.load(sys.stdin)
try:
    env = {}
    with open(d['env'], encoding='utf-8') as f:
        for l in f:
            l = l.strip()
            if '=' in l and not l.startswith('#'):
                k, v = l.split('=', 1)
                env[k.strip()] = v.strip().strip('"').strip("'")
    base = [e.strip() for e in env.get('ALERTA_EMAIL_PARA', '').split(',') if e.strip()]
    para, vistos = [], set()
    for e in base + [x.strip() for x in d['extras']]:
        if e and e.lower() not in vistos:
            vistos.add(e.lower())
            para.append(e)
    usuario = env.get('ALERTA_SMTP_USUARIO', '').strip()
    senha = env.get('ALERTA_SMTP_SENHA', '').replace(' ', '')
    if not (base and usuario and senha):
        sys.exit('ALERTA_* incompleto no arquivo de segredos')
    m = EmailMessage()
    m['Subject'] = d['assunto']
    m['From'] = usuario
    m['To'] = ', '.join(para)
    m.set_content(d['texto'])
    m.add_alternative(d['html'], subtype='html')
    with smtplib.SMTP_SSL('smtp.gmail.com', 465, timeout=30) as s:
        s.login(usuario, senha)
        s.send_message(m)
except Exception as e:
    sys.exit('Falha no envio: ' + type(e).__name__)
print('E-mail enviado para %d destinatario(s).' % len(para))
"""


class ErroDeEnvio(Exception):
    """O e-mail não saiu; a mensagem traz só o motivo."""


def enviar_email(vps: str, arquivo_env_remoto: str, relatorio: Relatorio, extras: tuple[str, ...]) -> str:
    """Manda o relatório pelo servidor `vps` (usuário@endereço). Devolve a linha que o servidor respondeu."""
    codificado = base64.b64encode(PROGRAMA_REMOTO.encode("utf-8")).decode("ascii")
    comando = [
        "ssh",
        "-o",
        "BatchMode=yes",
        "-o",
        "ConnectTimeout=20",
        vps,
        f"python3 -c \"import base64; exec(base64.b64decode('{codificado}').decode())\"",
    ]
    conteudo = {
        "env": arquivo_env_remoto,
        "extras": list(extras),
        "assunto": relatorio.assunto,
        "texto": relatorio.texto,
        "html": relatorio.html,
    }
    try:
        resultado = subprocess.run(
            comando, input=json.dumps(conteudo).encode("utf-8"), capture_output=True, timeout=TEMPO_LIMITE, check=False
        )
    except subprocess.TimeoutExpired:
        raise ErroDeEnvio("O servidor não respondeu a tempo.") from None
    except OSError as e:
        raise ErroDeEnvio(f"Não consegui chamar o ssh ({type(e).__name__}).") from None
    if resultado.returncode != 0:
        linhas = resultado.stderr.decode("utf-8", errors="replace").strip().splitlines()
        motivo = linhas[-1] if linhas else f"o ssh terminou com o código {resultado.returncode}"
        raise ErroDeEnvio(motivo[:TAMANHO_DO_MOTIVO])
    return resultado.stdout.decode("utf-8", errors="replace").strip()


_NOTIFICACAO = r"""
[Windows.UI.Notifications.ToastNotificationManager, Windows.UI.Notifications, ContentType = WindowsRuntime] | Out-Null
$t = [Windows.UI.Notifications.ToastNotificationManager]::GetTemplateContent(
  [Windows.UI.Notifications.ToastTemplateType]::ToastText02)
$x = $t.GetElementsByTagName('text')
$x.Item(0).AppendChild($t.CreateTextNode($env:TITULO)) | Out-Null
$x.Item(1).AppendChild($t.CreateTextNode($env:AVISO)) | Out-Null
$app = '{1AC14E77-02E7-4E5D-B744-2EB1AE5198B7}\WindowsPowerShell\v1.0\powershell.exe'
[Windows.UI.Notifications.ToastNotificationManager]::CreateToastNotifier($app).Show(
  [Windows.UI.Notifications.ToastNotification]::new($t))
"""


def aviso_na_tela(titulo: str, texto: str) -> None:
    """Aviso na tela do Windows: com o servidor fora, o e-mail não sai, e ninguém saberia que a rotina falhou."""
    if sys.platform != "win32":
        return
    ambiente = {**os.environ, "TITULO": titulo, "AVISO": texto[:TAMANHO_DO_AVISO]}
    # Falhou o aviso: o código de saída 1 continua registrado no Agendador.
    with contextlib.suppress(OSError, subprocess.SubprocessError):
        subprocess.run(
            ["powershell.exe", "-NoProfile", "-NonInteractive", "-Command", _NOTIFICACAO],
            env=ambiente,
            capture_output=True,
            timeout=30,
            check=False,
        )
