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


@dataclass(frozen=True, slots=True)
class RegraProduto:
    """A regra de conversão de um produto: a matéria-prima e quantas unidades de controle vêm nele."""

    catalogo_id: str | None
    fator: float | None


@dataclass(frozen=True, slots=True)
class NotaForaDaConta:
    """Nota cujo boleto não fecha com os itens: o preço rateado seria falso."""

    chave_acesso: str
    numero: str
    data_emissao: str
    emitente_cnpj: str | None
    emitente_nome: str | None
    soma_itens: float
    soma_boleto: float


@dataclass(frozen=True, slots=True)
class LinhaDePreco:
    produto: str
    codigo_produto: str
    descricao: str
    unidade: str
    data_emissao: str
    chave_acesso: str
    posicao: int
    numero_nota: str
    preco: float
    quantidade: float
    materia_prima: str | None
    fator: float
    emitente_cnpj: str | None
    emitente_nome: str | None
    destinatario_cnpj: str | None


@dataclass(frozen=True, slots=True)
class Alerta:
    produto: str
    codigo_produto: str
    descricao: str
    unidade: str
    preco: float
    base: float
    alta: float
    a_mais: float
    quantidade: float
    numero_nota: str
    data_emissao: str
    como: str
    materia_prima: str | None
    fator: float
    emitente_cnpj: str | None
    emitente_nome: str | None
    destinatarios: tuple[str | None, ...]
    outras_notas: int
    chegou_depois: bool


@dataclass(frozen=True, slots=True)
class Relatorio:
    assunto: str
    texto: str
    html: str
