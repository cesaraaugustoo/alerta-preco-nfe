import contextlib
import io
import json
import subprocess
import tempfile
import unittest
from email.message import EmailMessage
from pathlib import Path
from typing import Any
from unittest import mock

from alerta_preco.envio import PROGRAMA_REMOTO, ErroDeEnvio, aviso_na_tela, enviar_email
from alerta_preco.modelos import Relatorio

SENHA = "abcd efgh ijkl mnop"
ENV_COMPLETO = f"""# comentario
OUTRA=nao-leia
ALERTA_EMAIL_PARA=primeira@exemplo.com, segunda@exemplo.com
ALERTA_SMTP_USUARIO="conta@exemplo.com"
ALERTA_SMTP_SENHA='{SENHA}'
"""
RELATORIO = Relatorio("Preços: nenhuma alta", "corpo em texto", "<p>corpo em html</p>")


class _SmtpFalso:
    enviadas: list[EmailMessage]
    logins: list[tuple[str, str]]
    erro: Exception | None = None

    def __init__(self, *_: object, **__: object) -> None:
        pass

    def __enter__(self) -> "_SmtpFalso":
        return self

    def __exit__(self, *_: object) -> None:
        pass

    def login(self, usuario: str, senha: str) -> None:
        if _SmtpFalso.erro:
            raise _SmtpFalso.erro
        _SmtpFalso.logins.append((usuario, senha))

    def send_message(self, mensagem: EmailMessage) -> None:
        _SmtpFalso.enviadas.append(mensagem)


class TestProgramaRemoto(unittest.TestCase):
    """Executa o programa que roda na VPS, com o servidor de e-mail simulado."""

    def setUp(self) -> None:
        pasta = tempfile.TemporaryDirectory()
        self.addCleanup(pasta.cleanup)
        self.env = Path(pasta.name) / "segredos.env"
        self.env.write_text(ENV_COMPLETO, encoding="utf-8")
        _SmtpFalso.enviadas, _SmtpFalso.logins, _SmtpFalso.erro = [], [], None

    def _executar(self, extras: list[str]) -> str:
        dados: dict[str, Any] = {
            "env": str(self.env),
            "extras": extras,
            "assunto": "Preços: nenhuma alta",
            "texto": "corpo em texto",
            "html": "<p>corpo em html</p>",
        }
        saida = io.StringIO()
        with (
            mock.patch("sys.stdin", io.StringIO(json.dumps(dados))),
            mock.patch("smtplib.SMTP_SSL", _SmtpFalso),
            contextlib.redirect_stdout(saida),
        ):
            exec(PROGRAMA_REMOTO, {"__name__": "__main__"})
        return saida.getvalue()

    def test_envia_texto_e_html(self) -> None:
        self._executar([])
        mensagem = _SmtpFalso.enviadas[0]
        self.assertEqual(mensagem["Subject"], "Preços: nenhuma alta")
        self.assertEqual(mensagem["From"], "conta@exemplo.com")
        texto, html = mensagem.get_body(("plain",)), mensagem.get_body(("html",))
        assert texto is not None and html is not None
        self.assertEqual(texto.get_content().strip(), "corpo em texto")
        self.assertEqual(html.get_content().strip(), "<p>corpo em html</p>")

    def test_a_senha_de_app_entra_sem_os_espacos(self) -> None:
        self._executar([])
        self.assertEqual(_SmtpFalso.logins, [("conta@exemplo.com", SENHA.replace(" ", ""))])

    def test_sem_extras_vai_so_para_os_do_arquivo(self) -> None:
        resposta = self._executar([])
        self.assertEqual(_SmtpFalso.enviadas[0]["To"], "primeira@exemplo.com, segunda@exemplo.com")
        self.assertIn("2 destinatario(s)", resposta)

    def test_extras_entram_sem_repetir_e_sem_diferenciar_maiusculas(self) -> None:
        resposta = self._executar(["pessoa@exemplo.com", "PRIMEIRA@exemplo.com", "Pessoa@Exemplo.com", " "])
        self.assertEqual(_SmtpFalso.enviadas[0]["To"], "primeira@exemplo.com, segunda@exemplo.com, pessoa@exemplo.com")
        self.assertIn("3 destinatario(s)", resposta)

    def test_arquivo_incompleto_nao_envia(self) -> None:
        self.env.write_text("ALERTA_EMAIL_PARA=primeira@exemplo.com\n", encoding="utf-8")
        with self.assertRaises(SystemExit) as saida:
            self._executar([])
        self.assertIn("ALERTA_", str(saida.exception.code))
        self.assertEqual(_SmtpFalso.enviadas, [])

    def test_so_extras_sem_o_destinatario_do_arquivo_nao_envia(self) -> None:
        self.env.write_text('ALERTA_SMTP_USUARIO=a@exemplo.com\nALERTA_SMTP_SENHA=x\n', encoding="utf-8")
        with self.assertRaises(SystemExit):
            self._executar(["pessoa@exemplo.com"])
        self.assertEqual(_SmtpFalso.enviadas, [])

    def test_falha_diz_so_o_tipo_do_erro(self) -> None:
        _SmtpFalso.erro = RuntimeError(f"recusada a senha {SENHA}")
        with self.assertRaises(SystemExit) as saida:
            self._executar([])
        self.assertIn("RuntimeError", str(saida.exception.code))
        self.assertNotIn("abcd", str(saida.exception.code))


class TestEnviarEmail(unittest.TestCase):
    def _enviar(self, resultado: Any, extras: tuple[str, ...] = ()) -> tuple[str, mock.MagicMock]:
        with mock.patch("alerta_preco.envio.subprocess.run", side_effect=[resultado]) as run:
            return enviar_email("usuario@servidor.exemplo", "/srv/segredos.env", RELATORIO, extras), run

    def _ok(self) -> subprocess.CompletedProcess[bytes]:
        return subprocess.CompletedProcess([], 0, b"E-mail enviado para 1 destinatario(s).\n", b"")

    def test_chama_o_ssh_sem_pedir_senha(self) -> None:
        _, run = self._enviar(self._ok())
        comando = run.call_args.args[0]
        self.assertEqual(comando[:6], ["ssh", "-o", "BatchMode=yes", "-o", "ConnectTimeout=20", "usuario@servidor.exemplo"])

    def test_o_conteudo_vai_pela_entrada_padrao(self) -> None:
        _, run = self._enviar(self._ok(), ("pessoa@exemplo.com",))
        dados = json.loads(run.call_args.kwargs["input"])
        self.assertEqual(dados["assunto"], RELATORIO.assunto)
        self.assertEqual(dados["texto"], RELATORIO.texto)
        self.assertEqual(dados["html"], RELATORIO.html)
        self.assertEqual(dados["env"], "/srv/segredos.env")
        self.assertEqual(dados["extras"], ["pessoa@exemplo.com"])
        self.assertNotIn(RELATORIO.texto, " ".join(run.call_args.args[0]))

    def test_devolve_a_resposta_da_vps(self) -> None:
        self.assertEqual(self._enviar(self._ok())[0], "E-mail enviado para 1 destinatario(s).")

    def test_erro_traz_so_a_ultima_linha_sem_conteudo_sensivel(self) -> None:
        falha = subprocess.CompletedProcess([], 1, b"", b"aviso qualquer\nFalha no envio: SMTPAuthenticationError\n")
        with self.assertRaises(ErroDeEnvio) as erro:
            self._enviar(falha)
        mensagem = str(erro.exception)
        self.assertEqual(mensagem, "Falha no envio: SMTPAuthenticationError")
        for sensivel in (RELATORIO.texto, RELATORIO.html, "base64", "aviso qualquer"):
            self.assertNotIn(sensivel, mensagem)

    def test_erro_sem_texto_diz_o_codigo(self) -> None:
        with self.assertRaisesRegex(ErroDeEnvio, "255"):
            self._enviar(subprocess.CompletedProcess([], 255, b"", b""))

    def test_sem_ssh_ou_sem_resposta_vira_erro_de_envio(self) -> None:
        for excecao in (FileNotFoundError("ssh"), subprocess.TimeoutExpired("ssh segredo", 1)):
            with mock.patch("alerta_preco.envio.subprocess.run", side_effect=excecao), self.assertRaises(ErroDeEnvio) as erro:
                enviar_email("v", "/e", RELATORIO, ())
            self.assertNotIn("segredo", str(erro.exception))


class TestAvisoNaTela(unittest.TestCase):
    def test_no_windows_chama_o_powershell_com_o_texto_no_ambiente(self) -> None:
        with mock.patch("alerta_preco.envio.sys.platform", "win32"), mock.patch("alerta_preco.envio.subprocess.run") as run:
            aviso_na_tela("Titulo", "x" * 300)
        self.assertEqual(run.call_args.args[0][0], "powershell.exe")
        ambiente = run.call_args.kwargs["env"]
        self.assertEqual(ambiente["TITULO"], "Titulo")
        self.assertEqual(ambiente["AVISO"], "x" * 250)
        self.assertNotIn("Titulo", " ".join(run.call_args.args[0]))

    def test_fora_do_windows_nao_faz_nada(self) -> None:
        with mock.patch("alerta_preco.envio.sys.platform", "linux"), mock.patch("alerta_preco.envio.subprocess.run") as run:
            aviso_na_tela("Titulo", "texto")
        run.assert_not_called()

    def test_falha_no_aviso_nao_derruba_a_rotina(self) -> None:
        with (
            mock.patch("alerta_preco.envio.sys.platform", "win32"),
            mock.patch("alerta_preco.envio.subprocess.run", side_effect=OSError("sem powershell")),
        ):
            aviso_na_tela("Titulo", "texto")


if __name__ == "__main__":
    unittest.main()
