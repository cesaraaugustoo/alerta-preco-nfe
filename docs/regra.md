# A regra do alerta

Como o programa decide que um preço subiu. Todos os nomes e valores abaixo são inventados.

## Quais notas entram na conta

Nota a nota, nesta ordem:

1. **Itens válidos:** valor do item maior que zero e quantidade maior que zero. Nota sem item válido fica fora (bonificação).
2. **Fora por decisão:** nota cancelada, no banco ou no serviço de notas, ou presente na tabela de notas fora.
3. **Boleto contra itens:** `f = soma(valor do item na nota) / soma(valor do produto)`. Com `f` abaixo de 0,97 ou acima de 1,35, a nota
   fica fora do cálculo: o boleto é rateado entre os itens, e isso criaria um preço falso.

A nota que cai no filtro 3 aparece em "Não entrou na conta". A exceção são os fornecedores de `fornecedores_boleto_conhecido`, na
configuração local: o boleto deles difere dos itens de forma permanente, e o aviso semanal viraria ruído. A nota continua fora do cálculo
de preço, mas vira só uma contagem no rodapé. Nota de outro fornecedor com boleto diferente continua sendo avisada.

## O preço e a base

- **Produto:** raiz do CNPJ do fornecedor, código e unidade. O código fica como veio: `007` e `7` são produtos diferentes.
- **Preço:** valor do item na nota dividido pela quantidade.
- **Base:** a mediana das 4 compras anteriores do mesmo produto, de datas anteriores à da linha. A mediana não deixa um preço errado virar
  o novo normal.

Exemplo, com as compras anteriores a R$ 100,00, R$ 100,00, R$ 110,00 e R$ 125,00: a base é R$ 105,00.

## Produto novo: a troca

Sem compra anterior do mesmo produto, a comparação é pela matéria-prima, usando o fator da regra de conversão:

```
base = mediana(preço / fator das 4 últimas compras da matéria-prima) × fator da linha
```

O alerta sai como "trocou" e aparece à parte no relatório: é aviso, não aumento. O valor da troca nunca entra no total de altas.

**Todas as notas do produto na rodada em que ele estreia usam a mesma base da primeira nota.** Exemplo: a matéria-prima vinha a R$ 100,00;
o produto novo chega a R$ 131,00, com 13 caixas na segunda-feira e 39 na quarta. O valor da troca é 52 × R$ 31,00 = R$ 1.612,00. Na rodada
seguinte, o produto já tem histórico e volta a ser comparado com ele mesmo.

## Quando vira alerta

Na rodada, as linhas com alta positiva são agrupadas por produto: `(preço - base) × quantidade` de todas elas é somado, e a linha de maior
alta dá o percentual, o preço e a base do alerta.

Vira alerta com **alta de 5% ou mais e soma de R$ 20,00 ou mais**. Os dois limites valem nos valores exatos.

**Não repete:** com o preço a até 2% do último preço avisado desse produto, não avisa de novo. Sem isso, um aumento de verdade voltaria por
semanas, até a mediana alcançá-lo.

## Mudanças na regra

| Quando | O que mudou | Por quê |
|---|---|---|
| 30/10/2026 | Produto que estreia na rodada: todas as notas usam a base da primeira. Antes, a segunda nota era comparada com a estreia, e a alta dela sumia ou encolhia | O valor mostrado cobria só parte do que a troca custou. Efeito colateral: como as notas agora somam, uma estreia em notas pequenas pode passar do mínimo de R$ 20,00 e virar alerta |
