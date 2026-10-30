"""A linha de comando e a orquestração da rodada."""

import argparse
import io
import json
import sys
from dataclasses import dataclass
from datetime import date, timedelta
from pathlib import Path

from . import config, regra
from .estado import (
    ErroDeEstado,
    avisados_da_rodada,
    carregar_estado,
    ja_enviada,
    notas_da_rodada,
    segunda_da_semana,
    semana_anterior,
    semanas_entre,
)
from .fontes import Banco, ErroDeFonte, canceladas_no_espiao, ler_credenciais, ler_tabelas
from .modelos import Relatorio
from .relatorio import DadosDoRelatorio, brl, dia_e_mes, montar_relatorio, numero
from .xml_nota import conferir_leitura, ler_pasta

DIFERENCAS_NA_TELA = 20


def _hoje() -> date:
    return date.today()


def _conferir_leitura(arquivo: Path) -> int:
    pasta = config.carregar_config().pasta_notas
    gabarito = json.loads(arquivo.read_text(encoding="utf-8"))
    c = conferir_leitura(pasta, gabarito)
    print(f"Arquivos comparados: {c.arquivos}; iguais: {c.iguais} de {c.arquivos}.")
    print(f"Notas lidas: {c.notas}; itens: {c.itens}.")
    print(f"Soma dos itens: JavaScript {brl(c.total_javascript)}; Python {brl(c.total_python)}.")
    for titulo, nomes in (("Só no gabarito", c.so_no_gabarito), ("Só na pasta", c.so_na_pasta)):
        if nomes:
            print(f"{titulo}: {len(nomes)}.", *nomes[:DIFERENCAS_NA_TELA], sep="\n  ")
    if c.diferencas:
        print(f"Diferenças: {len(c.diferencas)}.", *c.diferencas[:DIFERENCAS_NA_TELA], sep="\n  ")
    print("Tudo igual." if c.tudo_igual else "HÁ DIFERENÇAS.")
    return 0 if c.tudo_igual else 1


def _simular(de_texto: str, ate_texto: str) -> int:
    """Repete as rodadas semana a semana, sem o serviço de notas, sem estado e sem e-mail."""
    de, ate = date.fromisoformat(de_texto), date.fromisoformat(ate_texto)
    cfg = config.carregar_config()
    banco = Banco(ler_credenciais(cfg.arquivo_env))
    tabelas = ler_tabelas(cfg.pasta_negocio)
    leitura = ler_pasta(cfg.pasta_notas, (de - timedelta(days=config.DIAS_DE_HISTORICO)).isoformat(), ate.isoformat())
    filtro = regra.filtrar_notas(leitura.notas, banco.canceladas(), tabelas.notas_fora, tabelas.apelidos)
    semanas = regra.simular(regra.linhas_de_preco(filtro.validas, banco.regras()), semanas_entre(de, ate))

    print(
        f"Notas únicas: {len(leitura.notas)}; ilegíveis: {leitura.ilegiveis}; na conta: {len(filtro.validas)}; "
        f"sem item válido: {filtro.sem_item_valido}; canceladas: {filtro.canceladas}; "
        f"na tabela de fora: {filtro.fora_da_tabela}; boleto diferente: {len(filtro.fora_por_boleto)}."
    )
    for semana in semanas:
        print(f"\nDe {dia_e_mes(semana.de)} a {dia_e_mes(semana.ate)}: {len(semana.alertas)} alerta(s).")
        for a in semana.alertas:
            print(
                f"  {a.codigo_produto} | {a.descricao} | base {numero(a.base)} | preço {numero(a.preco)} | "
                f"{numero(a.alta * 100, 1)}% | {brl(a.a_mais)} | {a.como}"
            )
    print(f"\nPor semana: {', '.join(str(len(s.alertas)) for s in semanas)}")
    print(f"Total: {sum(len(s.alertas) for s in semanas)} alerta(s).")
    return 0


@dataclass(frozen=True, slots=True)
class _Rodada:
    relatorio: Relatorio
    chaves: set[str]
    avisados: dict[str, float]


def _montar_rodada(segunda: date, hoje: date, teste: bool = False, reenviar: bool = False) -> _Rodada:
    """Lê as fontes e calcula o relatório da semana. Não envia e não grava estado."""
    cfg = config.carregar_config()
    estado = carregar_estado()
    banco = Banco(ler_credenciais(cfg.arquivo_env))
    tabelas = ler_tabelas(cfg.pasta_negocio)
    fim = segunda + timedelta(days=6)

    # Lê até hoje, e não até o fim da semana, para o rodapé dizer até onde a pasta chegou.
    leitura = ler_pasta(
        cfg.pasta_notas,
        (segunda - timedelta(days=config.DIAS_DE_HISTORICO)).isoformat(),
        max(hoje, fim).isoformat(),
    )
    datas = {n.chave_acesso: n.data_emissao for n in leitura.notas}
    rodada = notas_da_rodada(datas.items(), segunda, estado)
    inicio = min([segunda.isoformat(), *(datas[c] for c in rodada.atrasadas)])
    canceladas, falhas = canceladas_no_espiao(banco.configs_espiao(), inicio, fim.isoformat())

    filtro = regra.filtrar_notas(leitura.notas, banco.canceladas() | canceladas, tabelas.notas_fora, tabelas.apelidos)
    linhas = regra.linhas_de_preco(filtro.validas, banco.regras())
    # No ensaio de uma semana já enviada, os alertas dela não podem ser suprimidos pelo próprio aviso.
    avisados = avisados_da_rodada(estado, segunda, reenviar or ja_enviada(estado, segunda))
    avaliacao = regra.avaliar_rodada(linhas, rodada.chaves, avisados, rodada.atrasadas)

    relatorio = montar_relatorio(
        DadosDoRelatorio(
            de=segunda.isoformat(),
            ate=fim.isoformat(),
            alertas=avaliacao.alertas,
            fora_por_boleto=[n for n in filtro.fora_por_boleto if n.chave_acesso in rodada.chaves],
            falhas_de_cancelamento=len(falhas),
            notas_na_rodada=len(rodada.chaves),
            produtos_sem_base=avaliacao.produtos_sem_base,
            nota_mais_nova=max(datas.values(), default=None),
            hoje=hoje,
            apelidos=tabelas.apelidos,
            lojas=tabelas.lojas,
            insumos=banco.insumos(),
            teste=teste,
        )
    )
    return _Rodada(relatorio, rodada.chaves, avaliacao.avisados)


def _salvar_previa(relatorio: Relatorio, segunda: date) -> tuple[Path, Path]:
    config.PASTA_LOCAL.mkdir(parents=True, exist_ok=True)
    texto = config.PASTA_LOCAL / f"{segunda.isoformat()}.txt"
    pagina = config.PASTA_LOCAL / f"{segunda.isoformat()}.html"
    texto.write_text(relatorio.texto, encoding="utf-8", newline="\n")
    pagina.write_text(relatorio.html, encoding="utf-8", newline="\n")
    return texto, pagina


def _ensaio(semana: str | None) -> int:
    """Mostra e salva a prévia. Não envia nem grava estado."""
    hoje = _hoje()
    segunda = segunda_da_semana(date.fromisoformat(semana)) if semana else semana_anterior(hoje)
    rodada = _montar_rodada(segunda, hoje)
    texto, pagina = _salvar_previa(rodada.relatorio, segunda)
    print(f"Assunto: {rodada.relatorio.assunto}\n\n{rodada.relatorio.texto}")
    print(f"(Ensaio: nada enviado. Prévia em {texto} e {pagina}.)")
    return 0


def main(argumentos: list[str] | None = None) -> int:
    if isinstance(sys.stdout, io.TextIOWrapper):  # no Windows, o padrão não é UTF-8
        sys.stdout.reconfigure(encoding="utf-8")
    parser = argparse.ArgumentParser(prog="alerta_preco", description="Relatório semanal de altas de preço.")
    parser.add_argument("--semana", metavar="AAAA-MM-DD", help="a semana desse dia; sem isto, a semana passada")
    parser.add_argument(
        "--conferir-leitura", type=Path, metavar="ARQUIVO", help="compara a leitura com a do JavaScript"
    )
    parser.add_argument("--simular", nargs=2, metavar=("DE", "ATE"), help="repete as rodadas de um período, sem enviar")
    args = parser.parse_args(argumentos)
    try:
        if args.conferir_leitura:
            return _conferir_leitura(args.conferir_leitura)
        if args.simular:
            return _simular(*args.simular)
        return _ensaio(args.semana)
    except (config.ErroDeConfiguracao, ErroDeFonte, ErroDeEstado, OSError, ValueError) as e:
        print(e, file=sys.stderr)
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
