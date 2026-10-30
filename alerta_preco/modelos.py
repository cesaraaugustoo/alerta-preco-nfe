"""Os dados que passam entre os módulos."""

from dataclasses import dataclass


@dataclass(frozen=True, slots=True)
class Item:
    codigo_produto: str | None
    descricao: str
    quantidade: float
    unidade: str
    valor_total: float
    valor_nf_item: float
    quantidade_tributada: float | None
    unidade_tributada: str | None


@dataclass(frozen=True, slots=True)
class Nota:
    chave_acesso: str
    numero: str
    data_emissao: str
    natureza_operacao: str | None
    valor_fatura: float
    emitente_cnpj: str | None
    emitente_nome: str | None
    destinatario_cnpj: str | None
    itens: tuple[Item, ...]
