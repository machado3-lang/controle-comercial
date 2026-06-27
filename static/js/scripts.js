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

function confirmarExclusao(url) {
    console.log('confirmarExclusao called with url:', url);
    const form = document.getElementById('formExcluirModal');
    if (!form) {
        console.error('formExcluirModal not found in DOM');
        alert('Erro: formulário de exclusão não encontrado');
        return;
    }
    form.action = url;
    const modalEl = document.getElementById('modalConfirmarExclusao');
    if (!modalEl) {
        console.error('modalConfirmarExclusao not found in DOM');
        alert('Erro: modal de exclusão não encontrado');
        return;
    }
    const modal = new bootstrap.Modal(modalEl);
    modal.show();
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
    fetch(url, { method: 'POST' })
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
    fetch(url, {
        method: 'POST',
        credentials: 'include',
        headers: {'Content-Type': 'application/x-www-form-urlencoded'},
        body: new URLSearchParams({senha: senha})
    })
    .then(r => r.json())
    .then(data => {
        if (data.success) {
            showToast(data.message || 'Operação concluída', 'success');
            bootstrap.Modal.getInstance(document.getElementById('modalConfirmarExclusao')).hide();
            setTimeout(() => window.location.reload(), 1000);
        } else {
            showToast(data.error || 'Erro ao excluir', 'danger');
        }
    })
    .catch(() => showToast('Erro na requisição', 'danger'));
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
