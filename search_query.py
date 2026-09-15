"""Parameterized catalog search; no source file access."""
import re


def compile_search(query):
    clauses, args = [], []
    # Quoted phrases may follow a field prefix; unfinished quotes remain usable.
    tokens = re.findall(r'(?:[^\s"]+|"[^"]*(?:"|$))+',query)
    fields = {'name':'name','memo':'memo','tag':'tags','path':'path','meta':'metadata_json'}
    for token in tokens:
        negative = token.startswith('-') and len(token)>1
        token = token[1:] if negative else token
        field, sep, value = token.partition(':')
        value = (value if sep and field in (*fields,'ext','type') else token).replace('"','')
        if not value:
            continue
        if sep and field=='ext':
            value = '.'+value.lstrip('.').casefold()
            clause = 'substr(casefold(name),-?)=?'
            params = [len(value),value]
        elif sep and field=='type':
            clause, params = 'kind=?',[value.casefold()]
        else:
            columns = [fields[field]] if sep and field in fields else ['name','memo','tags']
            clause = '('+' OR '.join(f'instr(casefold({column}),?)>0' for column in columns)+')'
            params = [value.casefold()]*len(columns)
        clauses.append(('NOT ('+clause+')') if negative else clause)
        args.extend(params)
    return clauses,args
