"""Backend intent adapter for the shared actual binary regular-grid contract."""
import json
from frontend.shared.python.common_volume_grid import CommonVolumeGrid,decode_common_volume

def common_volume_intent(intent,product,descriptor):
 if not isinstance(intent,dict)or set(intent)!={'field','unit','token','resolution','lut','visible'}:raise ValueError('common volume intent has missing/extra fields')
 if __package__:
  from .canonical_render_packet import _lut,_bool
 else:
  from canonical_render_packet import _lut,_bool
 request=json.loads(product.request_json);metadata=json.loads(product.metadata_json)
 if product.kind!='volume' or product.token!=intent['token']:raise ValueError('common grid product kind/identity differs')
 grid=decode_common_volume(product.payload,request,metadata)
 unit=descriptor['fieldUnits'].get(grid.field)
 if grid.field!=intent['field']or unit!=intent['unit']or request['resolution']!=intent['resolution']:raise ValueError('common volume field/unit/resolution differs from pinned product')
 return grid,{'grid':grid.descriptor(unit),'lut':_lut(intent['lut'],allow_flat=True),'visible':_bool(intent['visible'],'volume visibility')}
