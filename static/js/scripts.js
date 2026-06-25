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
    const form = document.getElementById('formExcluirModal');
    form.action = url;
    const modal = new bootstrap.Modal(document.getElementById('modalConfirmarExclusao'));
    modal.show();
}

function confirmarExclusaoSimples(url) {
    if (confirm('Deseja realmente excluir este registro?')) {
        fetch(url, { method: 'POST', redirect: 'manual' })
            .then(r => {
                if (r.status === 303 || r.status === 302) {
                    const redirectUrl = r.headers.get('location');
                    if (redirectUrl) window.location.href = redirectUrl;
                } else if (r.status === 400 || r.status === 403) {
                    r.json().then(d => alert(d.detail || d.erro || 'Não é possível excluir'));
                } else {
                    window.location.reload();
                }
            })
            .catch(() => window.location.reload());
    }
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

function calcularTotal() {
    var servico = parseFloat(document.getElementById('valor_servico')?.value) || 0;
    var pecas = parseFloat(document.getElementById('valor_pecas')?.value) || 0;
    var total = servico + pecas;
    var campo = document.getElementById('valor_total');
    if (campo) {
        campo.value = total.toFixed(2);
    }
}
