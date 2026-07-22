import json, os, sys
ROOT=os.path.dirname(os.path.abspath(__file__)).replace('\design_work','')
SRC=os.path.join(ROOT,'src')
sys.path.insert(0,SRC)
from repograph.models import GraphStore
from repograph.retrieve.context import build_repo_context
store=GraphStore.load(os.path.join(ROOT,'output','graph.json'))
rows=[json.loads(l) for l in open(os.path.join(ROOT,'eval','dataset.jsonl'),encoding='utf-8') if l.strip()]
def one(pred,label):
    r=next(x for x in rows if pred(x))
    ctx=build_repo_context(store,r['question'])
    linked=ctx.get('linked') or []
    print(f'--- {label} id={r["id"]} mode={ctx.get("mode")} n_linked={len(linked)}')
    if linked:
        print('   linked[0] keys:',sorted(linked[0].keys()))
        print('   linked[0]:',{k:linked[0][k] for k in linked[0]})
        # does any linked item carry a plain "id" key?
        print('   any has "id" key:',any("id" in x for x in linked))
        print('   has entity_id:',any("entity_id" in x for x in linked),' has node_id:',any("node_id" in x for x in linked))
        print('   score field present:',any("score" in x for x in linked))
one(lambda x:x['subset']=='FZ_dev', 'FZ_dev')
one(lambda x:x['subset']=='AMB' and x.get('gold_behavior')=='should_autopick','AMB-autopick')
one(lambda x:x['subset']=='AMB' and x.get('gold_behavior')=='should_disambiguate','AMB-disamb')
# symbol-ish: try an L0 or any with gold_mode_class symbol
one(lambda x:x.get('gold_mode_class')=='symbol','symbol-mode')
