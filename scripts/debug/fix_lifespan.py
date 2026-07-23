import re

with open('app/core/lifespan.py', 'r') as f:
    content = f.read()

# Replace the @app.template_global section
old_pattern = r'        return response\n\s+# Add template globals\n\s+@app\.template_global\n\s+def csrf_token\(\):\n\s+# This will be populated by the CSRF middleware\n\s+# The request is available in the template context\n\s+return ""\n\s+\n\s+# Actually add the CSRF token to template context using a context processor\n\s+# We\'ll use a custom TemplateResponse class or context processor\n\s+from starlette\.templating import _TemplateResponse\s*\n\s*original_template_response = _TemplateResponse\s*\n\s*async def custom_template_response\(request, name, context=None, status_code=200, headers=None, media_type=None, background=None\):\s*if context is None:\s*context = {}\s*# Add CSRF token to context\s*context\["csrf_token"\] = getattr\(request\.state, "csrf_token", ""\)\s*return await original_template_response\(request, name, context, status_code, headers, media_type, background\)\s*app\.state\.templates\.TemplateResponse = custom_template_response'

new_content = '''        return response

    # Override TemplateResponse to inject CSRF token into all template contexts
    from starlette.templating import _TemplateResponse

    original_template_response = _TemplateResponse

    async def custom_template_response(request, name, context=None, status_code=200, headers=None, media_type=None, background=None):
        if context is None:
            context = {}
        # Add CSRF token to context
        context["csrf_token"] = getattr(request.state, "csrf_token", "")
        return await original_template_response(request, name, context, status_code, headers, media_type, background)

    app.state.templates.TemplateResponse = custom_template_response'''

content = re.sub(old_pattern, new_content, content, flags=re.DOTALL)

with open('app/core/lifespan.py', 'w') as f:
    f.write(content)

print('Done')