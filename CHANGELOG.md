# Histórico de mudanças

O formato segue o [Keep a Changelog](https://keepachangelog.com/pt-BR/1.1.0/), e as versões seguem o
[Versionamento Semântico](https://semver.org/lang/pt-BR/).

## [Não lançado]

### Adicionado

- Leitura do XML da NF-e e da pasta de notas.
- Regra de alertas, com os filtros de nota e a comparação pela mediana das últimas compras.
- Leitura do banco, do serviço de notas e das tabelas locais.
- Estado das rodadas, com a nota atrasada e a não repetição de avisos.
- Relatório em texto e HTML, e o ensaio da semana com prévia salva.
- `fornecedores_boleto_conhecido` na configuração local: o fornecedor cujo boleto difere dos itens de forma permanente vira só uma
  contagem no rodapé, em vez de um aviso toda semana. A nota continua fora do cálculo de preço.

### Alterado

- Produto que estreia na rodada como troca: todas as notas dele na rodada usam a mesma base da primeira. Antes, a segunda nota era
  comparada com a estreia, e o valor mostrado cobria só parte do que a troca custou. Como as notas agora somam, uma estreia em notas
  pequenas pode passar do mínimo e virar alerta. Detalhes em [`docs/regra.md`](docs/regra.md).
- Assunto e primeira linha do relatório separam o que subiu do que trocou. O valor das trocas não entra no total de altas.
