document.addEventListener('DOMContentLoaded', function() {
    var tooltipTriggerList = [].slice.call(document.querySelectorAll('[data-bs-toggle="tooltip"]'));
    tooltipTriggerList.map(function(el) {
        return new bootstrap.Tooltip(el);
    });

    document.querySelectorAll('input[name="cpf_cnpj"]').forEach(function(input) {
        input.addEventListener('input', function() {
            var tipo = this.closest('form').querySelector('select[name="tipo_pessoa"]');
            if (!tipo) return;
            aplicarMascaraDoc(this, tipo.value);
        });
    });

    document.querySelectorAll('select[name="tipo_pessoa"]').forEach(function(select) {
        select.addEventListener('change', function() {
            var doc = this.closest('form').querySelector('input[name="cpf_cnpj"]');
            if (doc) {
                doc.value = '';
                doc.maxLength = this.value === 'juridica' ? 18 : 14;
                aplicarMascaraDoc(doc, this.value);
            }
        });
        var doc = select.closest('form').querySelector('input[name="cpf_cnpj"]');
        if (doc) {
            doc.maxLength = select.value === 'juridica' ? 18 : 14;
        }
    });

    document.querySelectorAll('input[name="cep"]').forEach(function(input) {
        input.addEventListener('input', function() {
            aplicarMascaraCep(this);
        });
    });
});

function aplicarMascaraDoc(input, tipo) {
    var val = input.value.replace(/\D/g, '');
    if (tipo === 'juridica') {
        val = val.replace(/^(\d{2})(\d)/, '$1.$2');
        val = val.replace(/^(\d{2})\.(\d{3})(\d)/, '$1.$2.$3');
        val = val.replace(/\.(\d{3})(\d)/, '.$1/$2');
        val = val.replace(/(\d{4})(\d)/, '$1-$2');
        val = val.substring(0, 18);
    } else {
        val = val.replace(/^(\d{3})(\d)/, '$1.$2');
        val = val.replace(/^(\d{3})\.(\d{3})(\d)/, '$1.$2.$3');
        val = val.replace(/\.(\d{3})(\d)/, '.$1-$2');
        val = val.substring(0, 14);
    }
    input.value = val;
}

function aplicarMascaraCep(input) {
    var val = input.value.replace(/\D/g, '');
    val = val.replace(/^(\d{5})(\d)/, '$1-$2');
    val = val.substring(0, 9);
    input.value = val;
}

function getCsrfToken() {
    var meta = document.querySelector('meta[name="csrf-token"]');
    return meta ? meta.getAttribute('content') : '';
}

// Helper para fetch POST/PUT/PATCH/DELETE com CSRF automático (obrigatório pelo middleware).
function csrfFetch(url, options) {
    options = options || {};
    options.method = (options.method || 'POST').toUpperCase();
    options.credentials = 'include';
    var tok = getCsrfToken();
    var bodyIsJson = false;
    if (options.body instanceof URLSearchParams) {
        options.body.append('csrf_token', tok);
    } else if (options.body instanceof FormData) {
        if (!options.body.has('csrf_token')) options.body.append('csrf_token', tok);
    } else if (typeof options.body === 'string' && options.body.indexOf('csrf_token') === -1) {
        options.body += (options.body ? '&' : '') + 'csrf_token=' + encodeURIComponent(tok);
    } else if (options.body !== undefined && options.body !== null && typeof options.body === 'object' && !Array.isArray(options.body) && !(options.body instanceof Blob)) {
        // JSON object (application/json): o middleware não lê token do corpo JSON,
        // então enviamos na query string.
        bodyIsJson = true;
    } else if (options.body === undefined || options.body === null) {
        options.body = new URLSearchParams({csrf_token: tok});
        if (!options.headers) options.headers = {};
        options.headers['Content-Type'] = 'application/x-www-form-urlencoded';
    }
    // Anexa o token na query string (aceito pelo middleware como fallback).
    if (url.indexOf('csrf_token=') === -1) {
        url += (url.indexOf('?') === -1 ? '?' : '&') + 'csrf_token=' + encodeURIComponent(tok);
    }
    return fetch(url, options);
}

function confirmarExclusao(url) {
    const form = document.getElementById('formExcluirModal');
    if (!form) {
        alert('Erro: formulário de exclusão não encontrado');
        return;
    }
    form.action = url;
    const aviso = document.getElementById('avisoExclusaoContas');
    const chk = document.getElementById('excluirContas');
    if (chk) chk.checked = false;
    if (aviso) aviso.classList.add('hidden');
    // Se for exclusão de pedido, verifica se há contas a receber vinculadas.
    const m = url.match(/\/pedidos\/(\d+)\/excluir$/);
    if (m && aviso) {
        fetch('/pedidos/' + m[1] + '/contas-vinculadas', {credentials: 'include'})
            .then(r => r.ok ? r.json() : null)
            .then(d => { if (d && d.tem_contas) aviso.classList.remove('hidden'); })
            .catch(() => {});
    }
    const modalEl = document.getElementById('modalConfirmarExclusao');
    if (!modalEl) {
        alert('Erro: modal de exclusão não encontrado');
        return;
    }
    const modal = new bootstrap.Modal(modalEl);
    modal.show();
}

function confirmarEstorno(url) {
    if (!confirm('Estornar esta baixa fará a conta voltar para "em aberto". Confirma?')) return;
    csrfFetch(url, { method: 'POST' })
        .then(r => r.redirected ? window.location.href = r.url : window.location.reload())
        .catch(() => window.location.reload());
}

function abrirPagamento(id, descricao, fornecedor, valor) {
    document.getElementById('pagarDescricao').textContent = descricao;
    document.getElementById('pagarFornecedor').textContent = fornecedor;
    document.getElementById('pagarValor').textContent = 'R$ ' + valor.toFixed(2);
    const vp = document.getElementById('pagarValorPago');
    if (vp) vp.value = valor.toFixed(2);
    const dp = document.getElementById('pagarData');
    if (dp) dp.value = new Date().toISOString().split('T')[0];
    document.getElementById('formPagar').action = '/contas/pagar/' + id + '/baixar';
    calcularTotalPagar();
    const modal = new bootstrap.Modal(document.getElementById('modalPagar'));
    modal.show();
}

function calcularTotalPagar() {
    const num = id => { const el = document.getElementById(id); return el && parseFloat(el.value) ? parseFloat(el.value) : 0; };
    const total = num('pagarValorPago') + num('pagarJuros') - num('pagarDesconto');
    const el = document.getElementById('pagarTotal');
    if (el) el.textContent = 'R$ ' + total.toFixed(2).replace('.', ',');
}

function calcularTotalReceber() {
    const num = id => { const el = document.getElementById(id); return el && parseFloat(el.value) ? parseFloat(el.value) : 0; };
    const total = num('receberValorRecebido') + num('receberJuros') - num('receberDesconto');
    const el = document.getElementById('receberTotal');
    if (el) el.textContent = 'R$ ' + total.toFixed(2).replace('.', ',');
}

function confirmarExclusaoSimples(url) {
    if (!confirm('Deseja realmente excluir este registro?')) return;
    fetch(url, { method: 'POST', credentials: 'include', redirect: 'follow' })
        .then(r => {
            if (r.redirected) {
                window.location.href = r.url;
            } else if (r.status === 303 || r.status === 302) {
                const redirectUrl = r.headers.get('location');
                if (redirectUrl) {
                    window.location.href = redirectUrl;
                } else {
                    window.location.reload();
                }
            } else if (r.status === 400 || r.status === 403) {
                r.json().then(d => alert(d.detail || d.erro || 'Não é possível excluir'));
            } else {
                window.location.reload();
            }
        })
        .catch(() => window.location.reload());
}

function excluirDireto(url) {
    csrfFetch(url, { method: 'POST' })
        .then(r => {
            if (r.redirected) {
                window.location.href = r.url;
            } else if (r.status === 400) {
                r.json().then(d => alert(d.detail || 'Não é possível excluir'));
            } else {
                window.location.reload();
            }
        })
        .catch(() => window.location.reload());
}

function handleSubmitExclusao(form) {
    const url = form.action;
    const senha = form.senha.value;
    const csrf = form.csrf_token ? form.csrf_token.value : '';
    const excluirContas = document.getElementById('excluirContas') && document.getElementById('excluirContas').checked ? '1' : '';
    const btn = form.querySelector('button[type="submit"]');
    if (btn) { btn.disabled = true; btn.innerHTML = '<i data-lucide="loader" class="w-4 h-4 me-1"></i> Excluindo...'; if (window.lucide) lucide.createIcons(); }
    fetch(url, {
        method: 'POST',
        credentials: 'include',
        headers: {'Content-Type': 'application/x-www-form-urlencoded'},
        body: new URLSearchParams({senha: senha, csrf_token: csrf, excluir_contas: excluirContas})
    })
    .then(r => r.json().then(d => ({status: r.status, data: d})))
    .then(({status, data}) => {
        if (status === 403) {
            showToast(data.erro || data.error || 'Senha inválida', 'danger');
        } else if (data.ok || data.success) {
            showToast('Registro excluído', 'success');
            bootstrap.Modal.getInstance(document.getElementById('modalConfirmarExclusao')).hide();
            setTimeout(() => {
                if (data.redirect) {
                    window.location.href = data.redirect;
                } else {
                    // Permanece na página atual (a listagem de onde veio o modal).
                    window.location.href = window.location.pathname;
                }
            }, 500);
            return;
        } else {
            showToast(data.erro || data.error || 'Erro na requisição', 'danger');
        }
        if (btn) { btn.disabled = false; btn.innerHTML = '<i data-lucide="trash-2" class="w-4 h-4 me-1"></i> Confirmar Exclusão'; if (window.lucide) lucide.createIcons(); }
    })
    .catch(() => {
        showToast('Erro na requisição', 'danger');
        if (btn) { btn.disabled = false; btn.innerHTML = '<i data-lucide="trash-2" class="w-4 h-4 me-1"></i> Confirmar Exclusão'; if (window.lucide) lucide.createIcons(); }
    });
    return false;
}

function calcularTotal() {
    var servico = parseFloat(document.getElementById('valor_servico')?.value) || 0;
    var pecas = parseFloat(document.getElementById('valor_pecas')?.value) || 0;
    var total = servico + pecas;
    var campo = document.getElementById('valor_total');
    if (campo) {
        campo.value = total.toFixed(2);
    }
}

document.addEventListener('DOMContentLoaded', function () {
    ['pagarValorPago', 'pagarJuros', 'pagarDesconto'].forEach(function (id) {
        const el = document.getElementById(id);
        if (el) el.addEventListener('input', calcularTotalPagar);
    });
    ['receberValorRecebido', 'receberJuros', 'receberDesconto'].forEach(function (id) {
        const el = document.getElementById(id);
        if (el) el.addEventListener('input', calcularTotalReceber);
    });
});
