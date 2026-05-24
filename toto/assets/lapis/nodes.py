"""Small Python helpers to build Lapis JSON trees without hand-writing dicts."""

def Seq(steps): return {"type": "seq", "steps": steps}
def Assert(condition): return {"type": "assert", "condition": condition}
def If(condition, then, otherwise=None):
    node = {"type": "if", "condition": condition, "then": then}
    if otherwise is not None: node["else"] = otherwise
    return node

def Eq(a,b): return {"type":"eq","left":a,"right":b}
def Neq(a,b): return {"type":"neq","left":a,"right":b}
def Gt(a,b): return {"type":"gt","left":a,"right":b}
def Gte(a,b): return {"type":"gte","left":a,"right":b}
def Lt(a,b): return {"type":"lt","left":a,"right":b}
def Lte(a,b): return {"type":"lte","left":a,"right":b}
def And(*values): return {"type":"and","values":list(values)}
def Or(*values): return {"type":"or","values":list(values)}
def Not(value): return {"type":"not","value":value}
def Add(a,b): return {"type":"add","left":a,"right":b}
def Sub(a,b): return {"type":"sub","left":a,"right":b}
def Mul(a,b): return {"type":"mul","left":a,"right":b}
def Div(a,b): return {"type":"div","left":a,"right":b}

def Int(value): return {"type":"int","value":value}
def Decimal_(value): return {"type":"decimal","value":str(value)}
def Bytes(value): return {"type":"bytes","value":value}
def Bool(value): return {"type":"bool","value":value}
def Param(name): return {"type":"param","name":name}
def GetState(key): return {"type":"get_state","key":key}
def SetState(key, value): return {"type":"set_state","key":key,"value":value}
def Account(ref): return {"type":"account","ref":ref}
def Asset(ref): return {"type":"asset","ref":ref}
def Amount(ref): return {"type":"amount","ref":ref}
def Balance(account, asset): return {"type":"balance","account":account,"asset":asset}
def Transfer(asset, source, target, amount): return {"type":"transfer","asset":asset,"from":source,"to":target,"amount":amount}
def Record(kind, data=None): return {"type":"record","kind":kind,"data":data or {}}
def Oblig(debtor, creditor, asset, amount, due_at, role="", metadata=None):
    return {"type":"oblig","debtor":debtor,"creditor":creditor,"asset":asset,"amount":amount,"due_at":due_at,"role":role,"metadata":metadata or {}}

def Contract(name, actions, version=1):
    return {"language":"lapis","version":version,"name":name,"actions":actions}
