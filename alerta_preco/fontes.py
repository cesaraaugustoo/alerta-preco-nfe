"""As fontes além do XML: banco, serviço de notas e as tabelas em Markdown. Só leitura.

Erro nunca traz chave, token ou senha: as mensagens dizem o que falhou e o código HTTP, nada do pedido.
"""

import contextlib
import http.client
import json
import re
import time
import urllib.error
import urllib.parse
import urllib.request
from collections.abc import Callable, Iterable, Iterator, Mapping
from dataclasses import dataclass, field
from datetime import date, timedelta
from pathlib import Path
from typing import Any

from .modelos import RegraProduto
from .regra import chave_apelido

Abrir = Callable[[urllib.request.Request], Any]

AGENTE = "alerta-preco-nfe"
TAMANHO_DA_PAGINA = 1000
TEMPO_LIMITE = 30
VARIAVEL_URL = "VITE_SUPABASE_URL"
VARIAVEL_CHAVE = "SUPABASE_SERVICE_ROLE_KEY"
ESPIAO_URL = "https://api.espiaonfe.com.br/v1-cloud/consulta/periodo/nfe-resumo"
ESPIAO_DIAS_POR_CONSULTA = 30  # o serviço recusa período maior que 31 dias
ESPIAO_TENTATIVAS_429 = 4
ESPIAO_ESPERA_429 = 10


class ErroDeFonte(Exception):
    """Uma fonte obrigatória falhou; a rodada para, em vez de mandar um e-mail enganoso."""


def _abrir(pedido: urllib.request.Request) -> Any:
    return urllib.request.urlopen(pedido, timeout=TEMPO_LIMITE)


@dataclass(frozen=True, slots=True)
class Credenciais:
    url: str
    chave: str = field(repr=False)


def ler_credenciais(arquivo_env: Path) -> Credenciais:
    """Lê do arquivo só as duas variáveis do banco, em tempo de execução."""
    valores: dict[str, str] = {}
    try:
        linhas = arquivo_env.read_text(encoding="utf-8").splitlines()
    except OSError as e:
        raise ErroDeFonte(f"Não consegui ler o arquivo de segredos ({type(e).__name__}).") from None
    for linha in linhas:
        nome, igual, valor = linha.strip().partition("=")
        if igual and nome.strip() in (VARIAVEL_URL, VARIAVEL_CHAVE):
            valores[nome.strip()] = valor.strip().strip("\"'")
    for variavel in (VARIAVEL_URL, VARIAVEL_CHAVE):
        if not valores.get(variavel):
            raise ErroDeFonte(f"Falta {variavel} no arquivo de segredos.")
    return Credenciais(valores[VARIAVEL_URL].rstrip("/"), valores[VARIAVEL_CHAVE])


@dataclass(frozen=True, slots=True)
class ConfigEspiao:
    cnpj: str
    esp_cloud_token: str = field(repr=False)
    user_token: str = field(repr=False)


@contextlib.contextmanager
def _formato_esperado(tabela: str) -> Iterator[None]:
    """Linha fora do formato para a rodada: sem as regras ou as canceladas, a conta sairia errada e pareceria certa."""
    try:
        yield
    except (AttributeError, KeyError, TypeError, ValueError) as e:
        raise ErroDeFonte(f"Banco, tabela {tabela}: linha em formato inesperado ({type(e).__name__}).") from None


class Banco:
    def __init__(self, credenciais: Credenciais, abrir: Abrir = _abrir) -> None:
        self._credenciais = credenciais
        self._abrir = abrir

    def _ler(
        self, tabela: str, colunas: str, ordem: str, filtros: Mapping[str, str] | None = None
    ) -> list[dict[str, Any]]:
        linhas: list[dict[str, Any]] = []
        while True:
            parametros = {
                "select": colunas,
                "order": ordem,
                "limit": str(TAMANHO_DA_PAGINA),
                "offset": str(len(linhas)),
                **(filtros or {}),
            }
            pedido = urllib.request.Request(
                f"{self._credenciais.url}/rest/v1/{tabela}?{urllib.parse.urlencode(parametros)}",
                headers={
                    "apikey": self._credenciais.chave,
                    "Authorization": f"Bearer {self._credenciais.chave}",
                    "User-Agent": AGENTE,
                },
            )
            try:
                with self._abrir(pedido) as resposta:
                    pagina = json.load(resposta)
            except urllib.error.HTTPError as e:
                e.close()
                raise ErroDeFonte(f"Banco, tabela {tabela}: HTTP {e.code}.") from None
            except (OSError, ValueError, http.client.HTTPException) as e:
                raise ErroDeFonte(f"Banco, tabela {tabela}: sem resposta válida ({type(e).__name__}).") from None
            if not isinstance(pagina, list):
                raise ErroDeFonte(f"Banco, tabela {tabela}: a resposta não é uma lista.")
            linhas += pagina
            if len(pagina) < TAMANHO_DA_PAGINA:
                return linhas

    def _ler_cheia(
        self, tabela: str, colunas: str, ordem: str, filtros: Mapping[str, str] | None = None
    ) -> list[dict[str, Any]]:
        """Tabela que nunca fica vazia: se vier, a chave ou o acesso mudou, e a conta sairia errada sem nenhum erro."""
        linhas = self._ler(tabela, colunas, ordem, filtros)
        if not linhas:
            raise ErroDeFonte(f"Banco, tabela {tabela}: veio vazia. A chave do banco pode ter mudado.")
        return linhas

    def regras(self) -> dict[str, RegraProduto]:
        """As regras de conversão por "raiz|código". O código fica como veio: zero à esquerda distingue produto."""
        linhas = self._ler_cheia(
            "regra_produto", "cnpj_raiz,codigo_produto,catalogo_id,fator", "cnpj_raiz,codigo_produto"
        )
        with _formato_esperado("regra_produto"):
            return {
                f"{x['cnpj_raiz']}|{x['codigo_produto']}": RegraProduto(
                    None if x["catalogo_id"] is None else str(x["catalogo_id"]),
                    None if x["fator"] is None else float(x["fator"]),
                )
                for x in linhas
            }

    def insumos(self) -> dict[str, tuple[str, str]]:
        """O nome e a unidade de cada matéria-prima."""
        linhas = self._ler_cheia("insumo_catalogo", "id,nome,unidade", "id")
        with _formato_esperado("insumo_catalogo"):
            return {str(x["id"]): (x["nome"], x["unidade"]) for x in linhas}

    def canceladas(self) -> set[str]:
        linhas = self._ler("notas_fiscais", "chave_acesso", "chave_acesso", {"status": "eq.cancelada"})
        with _formato_esperado("notas_fiscais"):
            return {x["chave_acesso"] for x in linhas}

    def configs_espiao(self) -> list[ConfigEspiao]:
        linhas = self._ler_cheia(
            "integracao_espiao_config", "cnpj,esp_cloud_token,user_token", "cnpj", {"ativo": "eq.true"}
        )
        with _formato_esperado("integracao_espiao_config"):
            return [ConfigEspiao(x["cnpj"], x["esp_cloud_token"], x["user_token"]) for x in linhas]


def periodos_de_consulta(de: str, ate: str) -> list[tuple[str, str]]:
    """[de, ate] em pedaços de até 30 dias."""
    pedacos: list[tuple[str, str]] = []
    inicio, final = date.fromisoformat(de), date.fromisoformat(ate)
    while inicio <= final:
        fim = min(inicio + timedelta(days=ESPIAO_DIAS_POR_CONSULTA - 1), final)
        pedacos.append((inicio.isoformat(), fim.isoformat()))
        inicio += timedelta(days=ESPIAO_DIAS_POR_CONSULTA)
    return pedacos


def _consultar_periodo(
    config: ConfigEspiao, de: str, ate: str, abrir: Abrir, esperar: Callable[[float], None]
) -> tuple[set[str], str | None]:
    canceladas: set[str] = set()
    pagina: str | None = None
    tentativas = 0
    while True:
        parametros = {"cnpjCpf": config.cnpj, "dataInicial": de, "dataFinal": ate, "modelo": "55"}
        if pagina:
            parametros["codigoProximaPagina"] = pagina
        pedido = urllib.request.Request(
            f"{ESPIAO_URL}?{urllib.parse.urlencode(parametros)}",
            headers={
                "esp-cloud-token": config.esp_cloud_token,
                "user-token": config.user_token,
                "User-Agent": AGENTE,
            },
        )
        try:
            with abrir(pedido) as resposta:
                dados = json.load(resposta)
        except urllib.error.HTTPError as e:
            e.close()
            if e.code == 429 and tentativas < ESPIAO_TENTATIVAS_429:  # limite de requisições
                tentativas += 1
                esperar(ESPIAO_ESPERA_429)
                continue
            if e.code == 404:  # período sem nota
                return canceladas, None
            return canceladas, f"{config.cnpj} {de}: HTTP {e.code}"
        except (OSError, ValueError, TypeError, http.client.HTTPException) as e:
            return canceladas, f"{config.cnpj} {de}: {type(e).__name__}"
        por_dentro = dados.values() if isinstance(dados, dict) else ()
        notas = dados if isinstance(dados, list) else next((v for v in por_dentro if isinstance(v, list)), [])
        try:
            for nota in notas:
                situacao, chave = nota.get("situacao") or "", nota.get("chaveAcesso")
                if chave and re.search("cancel", situacao, re.IGNORECASE):
                    canceladas.add(chave)
        except (AttributeError, TypeError) as e:
            return canceladas, f"{config.cnpj} {de}: resposta em formato inesperado ({type(e).__name__})"
        proxima = dados.get("codigoProximaPagina") if isinstance(dados, dict) else None
        if not isinstance(proxima, str) or not proxima or proxima == "-1":
            return canceladas, None
        if proxima == pagina:  # sem isto, o laço só pararia no limite de tempo da tarefa, sem nenhum aviso
            return canceladas, f"{config.cnpj} {de}: a próxima página repetiu a atual"
        pagina = proxima


def canceladas_no_espiao(
    configs: Iterable[ConfigEspiao],
    de: str,
    ate: str,
    abrir: Abrir = _abrir,
    esperar: Callable[[float], None] = time.sleep,
) -> tuple[set[str], list[str]]:
    """As notas canceladas no serviço de notas e as falhas. Falha não interrompe: vira aviso no e-mail."""
    canceladas: set[str] = set()
    falhas: list[str] = []
    for config in configs:
        for inicio, fim in periodos_de_consulta(de, ate):
            achadas, falha = _consultar_periodo(config, inicio, fim, abrir, esperar)
            canceladas |= achadas
            if falha:
                falhas.append(falha)
    return canceladas, falhas


def _linhas(texto: str) -> list[str]:
    return re.split(r"\r?\n", texto)


def ler_apelidos(texto: str) -> dict[str, str]:
    """Raiz do CNPJ para apelido do fornecedor."""
    achados = (re.match(r"^\|\s*([0-9]{8})\s*\|\s*([^|]+?)\s*\|", linha) for linha in _linhas(texto))
    return {a[1]: a[2] for a in achados if a}


def ler_notas_fora(texto: str) -> set[str]:
    """As notas fora da conta, como "APELIDO|número"."""
    achados = (re.match(r"^\|\s*([^|]+?)\s*\|\s*([0-9]+)\s*\|", linha) for linha in _linhas(texto))
    return {f"{chave_apelido(a[1])}|{a[2]}" for a in achados if a}


def ler_lojas(texto: str) -> dict[str, str]:
    """CNPJ para loja: as linhas com "| razão social | CNPJ | loja |"."""
    padrao = r"^\|[^|]*\|\s*([0-9]{2}\.[0-9]{3}\.[0-9]{3}/[0-9]{4}-[0-9]{2})\s*\|\s*([^|]+?)\s*\|"
    achados = (re.match(padrao, linha) for linha in _linhas(texto))
    return {re.sub(r"[^0-9]", "", a[1]): a[2] for a in achados if a}


@dataclass(frozen=True, slots=True)
class Tabelas:
    apelidos: dict[str, str]
    notas_fora: set[str]
    lojas: dict[str, str]


def ler_tabelas(pasta_negocio: Path) -> Tabelas:
    """Lê as três tabelas em tempo de execução: nada delas é copiado para o código."""

    def texto(nome: str) -> str:
        try:
            return (pasta_negocio / nome).read_text(encoding="utf-8")
        except OSError as e:
            raise ErroDeFonte(f"Não consegui ler a tabela {nome} ({type(e).__name__}).") from None

    return Tabelas(
        apelidos=ler_apelidos(texto("11-apelidos-fornecedores.md")),
        notas_fora=ler_notas_fora(texto("15-notas-fora-do-balanco.md")),
        lojas=ler_lojas(texto("01-empresa.md")),
    )
