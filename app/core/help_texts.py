"""
Textos de ajuda dos campos do sistema (o "?" ao lado do label).

Fonte unica de verdade: o texto vive aqui, e nao dentro dos 90+ templates, para
poder reviser, versionar e reaproveitar. A chave segue `tela.campo` e e a mesma
usada na chamada `{{ ajuda('os.equipamento') }}` do template.

Regra de ouro do componente: chave inexistente nao quebra a tela — `ajuda()`
devolve string vazia e o botão simplesmente não aparece.

Texto curto (1 a 3 frases). "Como fazer" (passo a passo da tela) não cabe aqui:
esse conteúdo vive nos DOCUMENTACAO_*.md do repo.
"""

TEXTOS_AJUDA = {
    # ---- Cliente ----
    "cliente.codigo": (
        "Identificador interno do cliente. Já vem preenchido com o próximo número "
        "disponível; troque se você quiser outra numeração."
    ),
    "cliente.data_cadastro": (
        "Data em que o cliente entrou no sistema. É por ela que relatórios e listas "
        "são ordenados; num cadastro novo, normalmente é o dia de hoje."
    ),
    "cliente.tipo_pessoa": (
        "Física ou jurídica. Define se o documento é CPF ou CNPJ e ajusta o tamanho "
        "do campo. Empresa é sempre jurídica."
    ),
    "cliente.situacao": (
        "Só filtra a listagem de clientes. Não bloqueia a emissão de nota nem a "
        "cobrança — um cliente inativo ainda pode receber OS, pedido e nota."
    ),
    "cliente.nome": (
        "Nome do cliente ou razão social, exatamente como consta no documento "
        "fiscal. É este texto que sai impresso na NFe e na NFS-e."
    ),
    "cliente.fantasia": (
        "Ajuda a achar o cliente na busca, que procura por nome e por fantasia. "
        "A nota fiscal leva a razão social, não a fantasia."
    ),
    "cliente.contato": (
        "Pessoa de referência com quem falar. Fica só no cadastro, não vai para a nota."
    ),
    "cliente.cpf_cnpj": (
        "Documento do cliente. Ao sair do campo o sistema consulta a Receita e abre "
        "uma tela para você revisar e confirmar o preenchimento. Ao salvar, avisa se "
        "já existe cliente com o mesmo documento."
    ),
    "cliente.inscricao_estadual": (
        "IE do cliente. Sem ela a NFe é bloqueada — a menos que ele esteja marcado "
        "como isento de IE ou não contribuinte."
    ),
    "cliente.isento_ie": (
        "Marca que o cliente não tem Inscrição Estadual. Ao marcar, o Tipo "
        "Contribuinte passa sozinho para '2 - Isento'."
    ),
    "cliente.indicador_ie": (
        "Vai como indicador de IE na NFe: 1 contribute, 2 isento, 9 não contribuinte. "
        "É este campo que libera — ou barra — a emissão quando o cliente não tem IE."
    ),
    "cliente.inscricao_municipal": (
        "IM do cliente. Hoje é usada apenas na sincronização com o Bling: a NFS-e "
        "manda o município do emitente, não o do tomador."
    ),
    "cliente.iss_retido": (
        "Para cliente que retém o ISS na fonte. Ligado, a NFS-e nasce com iss_retido "
        "e a cobrança é gerada pelo valor líquido, já descontado o ISS."
    ),
    "cliente.tambem_fornecedor": (
        "Cria também um cadastro de Fornecedor com os mesmos dados, para o mesmo "
        "documento aparecer nas contas a pagar."
    ),
    "cliente.observacao": (
        "Anotação interna sobre o cliente. Aparece só na tela; não vai para a nota "
        "fiscal nem para o boleto."
    ),
    "cliente.cep": (
        "Preencha o CEP e o endereço é completado sozinho quando o campo perde o "
        "foco. O automático traz o logradouro; número e complemento são seus."
    ),
    "cliente.numero": "Número do endereço. Vem em branco pelo CEP — complete à mão.",
    "cliente.complemento": "Andar, sala, loja. O CEP preenche quando existe.",
    "cliente.endereco": "Logradouro. Preenchido pelo CEP ou pelos dados do CNPJ.",
    "cliente.bairro": "Bairro. Preenchido pelo CEP ou pelos dados do CNPJ.",
    "cliente.cidade": "Município. Preenchido pelo CEP ou pelos dados do CNPJ.",
    "cliente.estado": "UF. O CEP e o CNPJ preenchem sozinhos; confira o resultado.",
    "cliente.codigo_ibge": (
        "Código do município na base do IBGE (7 dígitos). Preenchido pelo CEP e "
        "usado na sincronização com o Bling."
    ),
    "cliente.telefone": "Telefone fixo. A máscara é aplicada sozinha enquanto você digita.",
    "cliente.celular": "Celular/WhatsApp. A máscara é aplicada sozinha enquanto você digita.",
    "cliente.email": (
        "E-mail para envio da NFS-e. Aceita vários separados por ponto e vírgula: "
        "os documentos vão para todos eles."
    ),

    # ---- Ordem de Serviço ----
    "os.cliente": (
        "Cliente que é dono do equipamento e que receberá a cobrança. A busca aceita "
        "nome ou CPF/CNPJ; o cadastro precisa existir antes."
    ),
    "os.equipamento": (
        "Descrição do aparelho que entrou para manutenção. É o campo que aparece "
        "primeiro na OS impressa e na tela de atendimento."
    ),
    "os.marca": "Fabricante do equipamento. Ajuda nas estatísticas de_OS e no histórico técnico.",
    "os.modelo": "Modelo/referência do aparelho. Ex.: \"Latitude 5420\".",
    "os.numero_serie": (
        "Número de série do equipamento que vai ser reparado — é este aparelho que "
        "a OS identifica. Localize a etiqueta física: é o que distingue dois "
        "aparelhos idênticos do mesmo cliente."
    ),
    "os.tecnico": "Quem executou o serviço. Aparece no impresso e no histórico da OS.",
    "os.autorizado_por": (
        "Nome de quem autorizou o serviço. Em garantia, costuma ser o próprio "
        "cliente: é o registro de que ele aprovou o orçamento."
    ),
    "os.numero_requisicao": (
        "Número da ordem de compra que o cliente emitiu autorizando este serviço. "
        "Muitos clientes (nem todos) geram uma OC e enviam junto do equipamento; "
        "quando existe, é ela que autoriza o reparo e o valor."
    ),
    "os.defeito_relatado": (
        "O que o cliente disse que estava errado, com as palavras dele. Fica no "
        "histórico técnico e não vai para a nota fiscal."
    ),
    "os.observacao": (
        "Anotação interna sobre o serviço. Aparece na OS e no impresso, mas não "
        "vai sozinha para a nota fiscal: isso depende da opção abaixo."
    ),
    "os.propagar_observacao": (
        "Define o padrão do botão \"Incluir nas notas\" que aparece na emissão da "
        "NFe e da NFSe desta OS. Desmarcado, a observação não vai para a nota. "
        "Depois que a nota é transmitida isso não muda mais: na NFe precisaria de "
        "carta de correção."
    ),
    "os.status": (
        "Andamento da OS. As transições são controladas: Aberta → Em andamento → "
        "Finalizada → Concluída, e Concluída pode ser cancelada. Emitir nota exige "
        "a OS concluída."
    ),
    "os.data_entrada": "Data em que o equipamento entrou. Aparece no recibo e no histórico.",
    "os.data_saida": (
        "Data prevista ou realizada da entrega. É a base do vencimento quando a "
        "cobrança é gerada sem primeiro vencimento informado."
    ),
    "os.valor_servico": (
        "Valor da mão de obra. É o que vira NFS-e; peças viram NFe."
    ),
    "os.valor_pecas": (
        "Valor total das peças. É o que vira NFe; a mão de obra vira NFS-e."
    ),
    "os.cobranca_separada": (
        "Ligado, a OS gera uma cobrança por nota (uma da NFe e outra da NFSe). "
        "Desligado, gera uma única cobrança agrupando as duas, com um boleto só."
    ),
    "os.forma_pagamento": (
        "Condição de pagamento da OS. Fica salva e é oferecida como padrão nas "
        "telas de emissão da NFe e da NFSe. Recibo sem nota (PIX, dinheiro) e "
        "garantia não sobrescrevem este valor."
    ),
    "os.num_parcelas": "Em quantas parcelas a cobrança é dividida. Boletos de parcelas diferentes são emitidos separadamente.",
    "os.intervalo_dias": "Dias entre o vencimento de uma parcela e o da seguinte.",
    "os.primeiro_vencimento": "Vencimento da 1ª parcela. Vazio usa a data de saída da OS.",
}


def texto_ajuda(chave: str):
    """Texto de ajuda da chave, ou None se ela nao existir."""
    return TEXTOS_AJUDA.get(chave)