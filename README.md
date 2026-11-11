# U-Go Backend

API, autenticação e regras de negócio do U-Go, desenvolvidas com Django, Django REST Framework e Channels. Este documento descreve como instalar, configurar, testar e operar o backend.

## Sumário

- [Responsabilidades](#responsabilidades)
- [Tecnologias](#tecnologias)
- [Estrutura](#estrutura)
- [Instalação local](#instalação-local)
- [Variáveis de ambiente](#variáveis-de-ambiente)
- [Banco e dados iniciais](#banco-e-dados-iniciais)
- [Autenticação e e-mail](#autenticação-e-e-mail)
- [Regras de carona](#regras-de-carona)
- [Match inteligente](#match-inteligente)
- [Notificações em tempo real](#notificações-em-tempo-real)
- [Perfis, imagens e badges](#perfis-imagens-e-badges)
- [API](#api)
- [Testes e verificações](#testes-e-verificações)
- [Tarefas operacionais](#tarefas-operacionais)
- [Checklist de produção](#checklist-de-produção)

## Responsabilidades

O backend é a fonte de verdade para:

- usuários, faculdades, cursos, sessões e verificação institucional;
- criação avulsa ou recorrente de caronas;
- disponibilidade e reserva concorrente de vagas;
- fluxo solicitação → aprovação → confirmação;
- visibilidade por gênero e proteção de dados sensíveis;
- busca e pontuação de compatibilidade;
- notificações persistentes e em tempo real;
- avaliações por badges, conquistas e impacto ambiental;
- estatísticas reais apresentadas na home.

As regras de autorização não dependem do frontend. Mesmo que uma chamada seja feita manualmente, a API valida usuário, papel, faculdade, gênero, estado da carona e prazo aplicável.

## Tecnologias

- Python 3.12+
- Django 5.1.1
- Django REST Framework 3.15.2
- Channels 4 e Daphne/ASGI
- PostgreSQL em produção; SQLite em desenvolvimento e testes
- Redis para o channel layer distribuído
- Pillow para validação e normalização de imagens
- django-storages e boto3 para S3 ou serviço compatível

As versões completas estão fixadas em `requirements.txt`.

## Estrutura

```text
backend/
├── .env.example
├── requirements.txt
└── u_go/
    ├── manage.py
    ├── u_go/
    │   ├── asgi.py
    │   ├── settings.py
    │   └── urls.py
    └── website/
        ├── management/commands/ # Lembretes e retenção
        ├── migrations/          # Evolução do banco
        ├── services/            # Match, notificações e conquistas
        ├── admin.py
        ├── consumers.py         # WebSocket autenticado
        ├── models.py
        ├── serializers.py
        ├── tests.py
        ├── urls.py
        └── views.py
```

## Instalação local

Os comandos abaixo usam PowerShell e partem da raiz do repositório.

```powershell
Copy-Item .env.example .env
py -3.12 -m venv .venv
.venv\Scripts\Activate.ps1
python -m pip install --upgrade pip
pip install -r requirements.txt
```

Para a primeira execução sem PostgreSQL, defina `USE_SQLITE=True` no `.env`.

```powershell
cd u_go
python manage.py migrate
python manage.py runserver 127.0.0.1:8000
```

Endereços locais:

- API: `http://127.0.0.1:8000/api/`
- Admin: `http://127.0.0.1:8000/admin/`
- mídia em debug: `http://127.0.0.1:8000/media/`

Para criar um administrador:

```powershell
python manage.py createsuperuser
```

## Variáveis de ambiente

O Django carrega `backend/.env`. Copie sempre o modelo `.env.example` e mantenha os segredos fora do repositório.

### Aplicação e segurança

| Variável | Finalidade |
| --- | --- |
| `DJANGO_SECRET_KEY` | Chave criptográfica exclusiva do ambiente. Obrigatória e aleatória em produção. |
| `DJANGO_DEBUG` | Ativa recursos locais. Deve ser `False` em produção. |
| `DJANGO_ALLOWED_HOSTS` | Hosts separados por vírgula aceitos pelo Django. |
| `FRONTEND_URL` | Origem pública do frontend, usada também nos links de recuperação. |
| `DJANGO_CSRF_TRUSTED_ORIGINS` | Origens HTTPS confiáveis para CSRF, separadas por vírgula. |
| `DJANGO_CORS_ALLOWED_ORIGINS` | Origens exatas permitidas pelo CORS. |
| `SESSION_COOKIE_SECURE` | Envia o cookie de sessão apenas por HTTPS. |
| `CSRF_COOKIE_SECURE` | Envia o cookie CSRF apenas por HTTPS. |
| `DJANGO_SECURE_SSL_REDIRECT` | Redireciona HTTP para HTTPS. |
| `DJANGO_SECURE_HSTS_SECONDS` | Duração do HSTS. Use apenas depois de confirmar HTTPS em todo o domínio. |

Com `DJANGO_DEBUG=False`, os padrões de cookies, redirecionamento e HSTS são seguros, mas declare os valores de produção explicitamente para facilitar auditoria operacional.

### Banco de dados

| Variável | Finalidade |
| --- | --- |
| `USE_SQLITE` | Usa `backend/u_go/db.sqlite3` quando `True`. Não use em produção. |
| `POSTGRES_DB` | Nome do banco PostgreSQL. |
| `POSTGRES_USER` | Usuário do banco. |
| `POSTGRES_PASSWORD` | Senha do banco. |
| `POSTGRES_HOST` | Host do banco. |
| `POSTGRES_PORT` | Porta do banco. |
| `POSTGRES_CONN_MAX_AGE` | Reutilização de conexões, em segundos. |

### E-mail

| Variável | Finalidade |
| --- | --- |
| `EMAIL_BACKEND` | Backend do Django; produção deve usar SMTP real. |
| `EMAIL_HOST` | Servidor SMTP. |
| `EMAIL_PORT` | Porta SMTP, normalmente 587 com TLS. |
| `EMAIL_HOST_USER` | Usuário SMTP. |
| `EMAIL_HOST_PASSWORD` | Senha ou token SMTP. |
| `EMAIL_USE_TLS` | Ativa STARTTLS. |
| `EMAIL_USE_SSL` | Ativa SSL direto; não pode coexistir com TLS. |
| `EMAIL_TIMEOUT` | Timeout de conexão em segundos. |
| `DEFAULT_FROM_EMAIL` | Remetente exibido. |
| `PASSWORD_RESET_TIMEOUT` | Validade do token de recuperação, em segundos. |

### Redis e WebSocket

| Variável | Finalidade |
| --- | --- |
| `REDIS_URL` | URL do Redis usada pelo Channels. Vazia usa channel layer em memória. |
| `NOTIFICATION_CHANNEL_CAPACITY` | Capacidade de mensagens por canal. |
| `NOTIFICATION_CHANNEL_EXPIRY` | Expiração de mensagens do channel layer, em segundos. |

O canal em memória só atende um processo. Redis é obrigatório para distribuir eventos entre múltiplas instâncias.

### Uploads e armazenamento

| Variável | Finalidade |
| --- | --- |
| `DJANGO_MEDIA_ROOT` | Diretório persistente para mídia local. Vazio usa `backend/u_go/media`. |
| `DJANGO_MAX_REQUEST_BYTES` | Limite total da requisição. |
| `DJANGO_FILE_MEMORY_BYTES` | Limite antes de o upload usar arquivo temporário. |
| `AWS_STORAGE_BUCKET_NAME` | Ao ser preenchida, habilita o storage S3 privado. |
| `AWS_S3_REGION_NAME` | Região do bucket. |
| `AWS_S3_ENDPOINT_URL` | Endpoint opcional para serviço compatível com S3. |
| `AWS_ACCESS_KEY_ID` | Credencial de acesso. Prefira identidade da infraestrutura quando disponível. |
| `AWS_SECRET_ACCESS_KEY` | Segredo de acesso. |
| `AWS_QUERYSTRING_EXPIRE` | Validade das URLs assinadas, em segundos. |

### Limites e métricas

| Variável | Finalidade |
| --- | --- |
| `THROTTLE_ANON_RATE` | Limite global de chamadas anônimas. |
| `THROTTLE_USER_RATE` | Limite global por usuário autenticado. |
| `THROTTLE_SIGNUP_RATE` | Limite de cadastros. |
| `THROTTLE_LOGIN_RATE` | Limite de tentativas de login. |
| `THROTTLE_VERIFICATION_RATE` | Limite de verificação e reenvio. |
| `THROTTLE_PASSWORD_RESET_RATE` | Limite de recuperação de senha. |
| `CO2_PER_AVOIDED_KM_KG` | Fator em kg de CO₂ por quilômetro evitado. Padrão: `0.192`. |
| `TREE_CO2_SEQUESTRATION_KG_PER_YEAR` | Captura média anual de CO₂ por árvore urbana usada na equivalência. Padrão: `60`, conforme metodologia da EPA. |

## Banco e dados iniciais

### PostgreSQL

Use `USE_SQLITE=False`, crie o banco e configure `POSTGRES_*`. Depois aplique:

```powershell
python manage.py migrate
```

PostgreSQL é necessário em produção porque os bloqueios de linha com `select_for_update()` protegem vagas e transições simultâneas. SQLite não oferece a mesma semântica de concorrência.

### Faculdade piloto

As migrações criam as tabelas, mas não inserem uma faculdade automaticamente. Em um banco novo, crie um superusuário, abra `/admin/` e cadastre:

- universidade: **USF Sagrado**;
- domínio, sem `@`: `mail.usf.edu.br`;
- cursos que devem aparecer no cadastro.

A API compara o domínio do e-mail com o domínio da faculdade selecionada; não basta digitar qualquer endereço com aparência acadêmica. Universidades e cursos podem ser administrados pelo Django Admin. Alterações de domínio devem ser planejadas para não bloquear alunos já cadastrados.

## Autenticação e e-mail

### Cadastro institucional

1. `POST /api/auth/signup/` valida campos, faculdade, domínio, senha, termos e privacidade.
2. A conta nasce pendente e recebe um código de seis dígitos por e-mail.
3. O código é armazenado somente como hash, expira em 15 minutos e aceita no máximo cinco tentativas.
4. `POST /api/auth/verify-email/` ativa a conta e abre uma sessão de navegador.
5. `POST /api/auth/resend-verification/` invalida o código anterior e gera um novo.

Em produção, o cadastro falha de forma transacional se o e-mail não puder ser entregue. Com `DJANGO_DEBUG=True`, respostas podem incluir campos de debug para facilitar desenvolvimento; nunca habilite debug publicamente.

### Login e “Lembrar de mim”

`POST /api/auth/login/` cria uma sessão Django protegida por cookie. Sem “Lembrar de mim”, a sessão expira ao fechar o navegador; com a opção marcada, a validade é de 14 dias. O logout revoga o registro da sessão e remove o cookie.

### Esqueci a senha

`POST /api/auth/password-reset/` sempre devolve uma mensagem neutra para não revelar quais e-mails existem. Para uma conta verificada, o backend envia um link para `FRONTEND_URL/login` com UID e token temporários. A confirmação ocorre em `POST /api/auth/password-reset/confirm/`; ao trocar a senha, sessões ainda ativas são revogadas.

## Regras de carona

### Criação

- carona avulsa exige data e hora;
- recorrência exige dias da semana, data inicial, data final e horário;
- o intervalo precisa produzir datas compatíveis com os dias escolhidos;
- cada ocorrência gera uma `Ride` e seus próprios assentos;
- o motorista informa veículo, modelo, placa, vagas, preço, observações e contato;
- se aceitar desvio, informa distância e tempo tolerados.

### Visibilidade e privacidade

- caronas sem exclusividade são públicas;
- caronas exclusivas aparecem e aceitam solicitações somente do gênero elegível;
- o motorista continua vendo as próprias caronas no acompanhamento;
- listagens públicas não retornam telefone ou placa;
- contato e dados operacionais são liberados apenas no match confirmado.

### Solicitação e confirmação

Estados principais da solicitação:

```text
pending -> approved -> confirmed
       \-> rejected
                 \-> declined
pending/approved/confirmed -> cancelled (com 24 h de antecedência)
```

A aprovação reserva a vaga; a confirmação do passageiro a ocupa e cria o vínculo de participação. Todas as mudanças críticas usam transação e bloqueio de linha.

### Edição, cancelamento e execução

- somente o motorista edita ou cancela a carona;
- edição e cancelamento exigem 24 horas de antecedência;
- participantes são notificados quando os dados mudam;
- passageiro pode cancelar a própria participação com o mesmo prazo; a vaga volta imediatamente a ficar disponível;
- somente o motorista inicia uma carona agendada e finaliza uma carona em andamento;
- a conclusão habilita avaliações por badges.

## Match inteligente

O serviço em `website/services/matching.py` pontua caronas usando:

- origem e destino normalizados;
- distância Haversine quando busca e carona possuem coordenadas;
- data, horário e flexibilidade em minutos;
- distância máxima tolerada;
- restrição por gênero;
- preferência de veículo e aceitação de desvio;
- preferências do perfil, buscas recentes e histórico de matches confirmados.

As caronas incompatíveis não entram nos recomendados, mas a busca ainda pode apresentar outras caronas ativas permitidas ao usuário. `POST /api/ride-searches/` registra o comportamento de busca, deduplicando pesquisas idênticas realizadas em até cinco minutos. Uma carona nova pode gerar notificação para usuários com busca compatível.

## Notificações em tempo real

As notificações são registros persistentes no banco. Cada evento possui uma chave de deduplicação e pertence a um único destinatário.

O WebSocket autenticado em `/ws/notifications/` envia apenas `notification.changed`. Ao receber o sinal, o frontend busca o conteúdo pela API REST; isso evita transmitir detalhes sensíveis em um canal genérico.

Em produção:

1. configure `REDIS_URL`;
2. execute o Django por ASGI;
3. encaminhe `/ws/` com suporte a upgrade de WebSocket;
4. preserve cookies e origem HTTPS;
5. monitore desconexões e latência do Redis.

```powershell
daphne -b 0.0.0.0 -p 8000 u_go.asgi:application
```

## Perfis, imagens e badges

### Perfil público e privado

O próprio usuário edita curso, semestre, descrição, telefone, endereço, avatar e capa em `/api/me/profile/`. Também pode salvar `avatar_config`, com opções permitidas de tom de pele; cor, estilo e tamanho do cabelo; expressão; barba ou bigode com cor própria; tipo e cor da roupa; e acessórios independentes em `headwear`, `eyewear`, `earAccessory` e `outfitAccessory`. O serializer rejeita chaves e valores fora do catálogo, força tamanho curto para cabelo raspado ou ausente e converte os formatos anteriores de cabelo, roupa, expressão e acessório. Trocar a exibição para o avatar vetorial não remove a foto enviada.

O perfil público em `/api/users/<uuid>/profile/` exige autenticação e restringe a consulta à mesma faculdade. E-mail, telefone e endereço não são serializados nessa resposta pública; `avatar_config` é público porque controla apenas a representação visual escolhida pelo usuário.

### Upload de imagens

Uploads aceitam JPG, PNG e WebP:

- avatar: até 5 MB, mínimo 64 × 64, máximo normalizado 1024 × 1024;
- capa: até 8 MB, mínimo 320 × 100, máximo normalizado 2400 × 1350.

O backend abre e valida o conteúdo real, corrige orientação, remove metadados e salva em WebP com nome aleatório. Em desenvolvimento, Django serve `/media/`. Em produção, use volume persistente com proxy web ou bucket privado S3; em múltiplas instâncias, prefira S3 compatível.

### Avaliações por badges

A API aceita avaliações somente quando:

- a carona está concluída;
- avaliador e avaliado participaram dela;
- o usuário não avalia a si mesmo;
- a mesma combinação de carona, avaliador, avaliado e badge ainda não existe.

Depois do feedback, as conquistas do destinatário são recalculadas. Não existe nota numérica.

O frontend associa ícones aos códigos estáveis dos badges. Certificados em PNG são oferecidos somente para conquistas cujo `achieved_at` foi retornado pela API e são gerados localmente, sem expor telefone, endereço ou e-mail.

## API

Todas as rotas abaixo usam o prefixo `/api/`.

| Grupo | Rotas principais |
| --- | --- |
| Autenticação | `auth/csrf/`, `auth/signup/`, `auth/login/`, `auth/logout/`, `auth/verify-email/`, `auth/resend-verification/`, `auth/password-reset/`, `auth/password-reset/confirm/` |
| Conta e perfil | `me/`, `me/profile/`, `users/<uuid>/profile/` |
| Catálogos | `universities/`, `courses/`, `badges/` |
| Caronas | `rides/`, `rides/<id>/`, `rides/tracking/`, `rides/<id>/start/`, `rides/<id>/complete/`, `rides/<id>/cancel/` |
| Solicitações | `ride-requests/`, `ride-requests/<id>/approve/`, `reject/`, `confirm/`, `decline/`, `cancel/` |
| Match | `ride-searches/` e consulta de `rides/` com `smart_match=true` |
| Notificações | `notifications/`, `notifications/<id>/mark_read/`, `notifications/mark_all_read/` |
| Avaliações | `ride-feedback/` |
| Home | `public/stats/` |

A API usa cookies de sessão. Clientes web devem primeiro obter o token em `/api/auth/csrf/`, enviar `X-CSRFToken` nas mutações e incluir credenciais nas requisições.

## Testes e verificações

Dentro de `backend/u_go`, com o ambiente virtual ativo:

```powershell
python manage.py test website.tests
python manage.py check
```

Verificação de implantação:

```powershell
python manage.py check --deploy
```

Execute `check --deploy` com as mesmas variáveis que serão usadas no servidor. Os testes cobrem autenticação, entrega de e-mail, perfis privados, upload, recorrência, exclusividade, concorrência de vagas, fluxo de match, cancelamentos, notificações, estatísticas e badges.

## Tarefas operacionais

### Lembretes

Cria lembretes deduplicados para motorista e passageiros confirmados em caronas que começam nas próximas 24 horas:

```powershell
python manage.py send_ride_reminders
```

Sugestão: agendar a cada cinco minutos.

### Retenção

Remove notificações e buscas antigas segundo os limites informados:

```powershell
python manage.py purge_old_notifications --notification-days 180 --search-days 90
```

Sugestão: executar uma vez ao dia. Ajuste os períodos à política de privacidade e às obrigações legais do ambiente.

## Checklist de produção

- [ ] `DJANGO_DEBUG=False` e segredo exclusivo.
- [ ] PostgreSQL com backup, restore testado e conexões monitoradas.
- [ ] Migrações aplicadas antes de servir tráfego.
- [ ] HTTPS, cookies seguros, HSTS, hosts, CORS e CSRF revisados.
- [ ] SMTP real com remetente autenticado e alertas de falha.
- [ ] Redis privado e WebSocket `/ws/` validado ponta a ponta.
- [ ] Mídia em storage persistente, privado e com política de retenção.
- [ ] Comandos periódicos agendados e monitorados.
- [ ] `manage.py check --deploy` sem avisos.
- [ ] Suíte de testes aprovada.
- [ ] Logs sem senhas, tokens, telefone, endereço ou conteúdo de sessão.
- [ ] Testes de carga e concorrência executados com volume realista.

Veja também o [repositório do frontend](https://github.com/biatorrecilas/u-go_front_end).
