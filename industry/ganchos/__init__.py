"""Ganchos para abordar os prospects clientes (pagina Clientes do home).

Um gancho e um fato recente e verificavel que liga a empresa a um dos dois
pacotes oferecidos, RAG sobre documentos ou dashboard de analytics. Cada fonte
e um asset do grupo `ganchos` e grava na tabela `ganchos` do Supabase. Nenhuma
fonte contata a empresa: so le dados publicos.

    vagas          vagas abertas que revelam dor de dados ou de documentacao
    rf             socio novo, capital maior, CNAE novo, mudanca de regime tributario
    filiais        filial aberta, lida dos zips mensais da Receita
    sites          paginas e PDFs novos ou atualizados no site
    financiamento  operacoes do BNDES
    inpi           marcas e patentes depositadas
"""
