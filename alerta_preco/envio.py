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
from dataclasses import dataclass

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
        recusados = s.send_message(m)
except smtplib.SMTPRecipientsRefused as e:
    sys.exit('Falha no envio: todos os destinatarios recusados: ' + ', '.join(e.recipients))
except Exception as e:
    sys.exit('Falha no envio: ' + type(e).__name__)
print('E-mail enviado para %d destinatario(s).' % (len(para) - len(recusados)))
for e in recusados:
    print('Recusado: ' + e.encode('ascii', 'replace').decode())
"""
_PREFIXO_DO_RECUSADO = "Recusado: "
# O ssh sai com 255 nos erros dele: com estas mensagens, a conexão nem abriu e o programa remoto não rodou.
_SEM_CONEXAO = ("connect to host", "Could not resolve hostname", "Permission denied", "Host key verification failed")


class ErroDeEnvio(Exception):
    """O e-mail não saiu, ou pode ter saído (`talvez_enviado`); a mensagem traz só o motivo."""

    def __init__(self, motivo: str, talvez_enviado: bool = False) -> None:
        super().__init__(motivo)
        self.talvez_enviado = talvez_enviado


@dataclass(frozen=True, slots=True)
class Envio:
    resposta: str
    recusados: tuple[str, ...] = ()


def enviar_email(vps: str, arquivo_env_remoto: str, relatorio: Relatorio, extras: tuple[str, ...]) -> Envio:
    """Manda o relatório pelo servidor `vps` (usuário@endereço). Devolve o que o servidor respondeu."""
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
        raise ErroDeEnvio("O servidor não respondeu a tempo.", talvez_enviado=True) from None
    except OSError as e:
        raise ErroDeEnvio(f"Não consegui chamar o ssh ({type(e).__name__}).") from None
    if resultado.returncode != 0:
        linhas = resultado.stderr.decode("utf-8", errors="replace").strip().splitlines()
        motivo = linhas[-1] if linhas else f"o ssh terminou com o código {resultado.returncode}"
        # Outro código vem do programa remoto, que só sai assim antes de enviar; 255 sem conexão aberta também.
        caiu_no_meio = resultado.returncode == 255 and not any(m in motivo for m in _SEM_CONEXAO)
        raise ErroDeEnvio(motivo[:TAMANHO_DO_MOTIVO], talvez_enviado=caiu_no_meio)
    linhas = resultado.stdout.decode("utf-8", errors="replace").strip().splitlines()
    recusados = tuple(x.removeprefix(_PREFIXO_DO_RECUSADO) for x in linhas if x.startswith(_PREFIXO_DO_RECUSADO))
    return Envio(next((x for x in linhas if not x.startswith(_PREFIXO_DO_RECUSADO)), ""), recusados)


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


# A faixa dura segundos e some na central de notificações. A janela fica sobre as outras, sem tempo limite, até
# alguém fechar: quem chegar depois das 8h10 ainda vê a falha.
_JANELA = "(New-Object -ComObject WScript.Shell).Popup($env:AVISO, 0, $env:TITULO, 0x1030) | Out-Null"
_SEM_CONSOLE = 0x08000000  # CREATE_NO_WINDOW; com DETACHED_PROCESS, o PowerShell termina na hora sem abrir nada
_GRUPO_PROPRIO = 0x00000200  # CREATE_NEW_PROCESS_GROUP


def aviso_na_tela(titulo: str, texto: str) -> None:
    """Faixa e janela no Windows: com o servidor fora, o e-mail não sai, e ninguém saberia que a rotina falhou."""
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
    with contextlib.suppress(OSError, subprocess.SubprocessError):
        subprocess.Popen(
            ["powershell.exe", "-NoProfile", "-NonInteractive", "-Command", _JANELA],
            env=ambiente,
            stdin=subprocess.DEVNULL,
            stdout=subprocess.DEVNULL,
            stderr=subprocess.DEVNULL,
            creationflags=_SEM_CONSOLE | _GRUPO_PROPRIO,
        )
