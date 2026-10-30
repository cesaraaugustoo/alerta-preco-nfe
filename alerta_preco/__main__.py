"""A linha de comando."""

import argparse
import io
import json
import sys
from datetime import date, timedelta
from pathlib import Path

from . import config, regra
from .estado import semanas_entre
from .fontes import Banco, ErroDeFonte, ler_credenciais, ler_tabelas
from .xml_nota import conferir_leitura, ler_pasta

DIFERENCAS_NA_TELA = 20


def _numero(valor: float) -> str:
    return f"{valor:,.2f}".replace(",", "_").replace(".", ",").replace("_", ".")


def _brl(valor: float) -> str:
    return f"R$ {_numero(valor)}"


def _dia_e_mes(data: str) -> str:
    return f"{data[8:10]}/{data[5:7]}"


def _conferir_leitura(arquivo: Path) -> int:
    pasta = config.carregar_config().pasta_notas
    gabarito = json.loads(arquivo.read_text(encoding="utf-8"))
    c = conferir_leitura(pasta, gabarito)
    print(f"Arquivos comparados: {c.arquivos}; iguais: {c.iguais} de {c.arquivos}.")
    print(f"Notas lidas: {c.notas}; itens: {c.itens}.")
    print(f"Soma dos itens: JavaScript {_brl(c.total_javascript)}; Python {_brl(c.total_python)}.")
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
        print(f"\nDe {_dia_e_mes(semana.de)} a {_dia_e_mes(semana.ate)}: {len(semana.alertas)} alerta(s).")
        for a in semana.alertas:
            print(
                f"  {a.codigo_produto} | {a.descricao} | base {_numero(a.base)} | preço {_numero(a.preco)} | "
                f"{_numero(a.alta * 100)}% | {_brl(a.a_mais)} | {a.como}"
            )
    print(f"\nPor semana: {', '.join(str(len(s.alertas)) for s in semanas)}")
    print(f"Total: {sum(len(s.alertas) for s in semanas)} alerta(s).")
    return 0


def main(argumentos: list[str] | None = None) -> int:
    if isinstance(sys.stdout, io.TextIOWrapper):  # no Windows, o padrão não é UTF-8
        sys.stdout.reconfigure(encoding="utf-8")
    parser = argparse.ArgumentParser(prog="alerta_preco", description="Relatório semanal de altas de preço.")
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
    except (config.ErroDeConfiguracao, ErroDeFonte, OSError, ValueError) as e:
        print(e, file=sys.stderr)
        return 1
    parser.print_help()
    return 1


if __name__ == "__main__":
    raise SystemExit(main())
