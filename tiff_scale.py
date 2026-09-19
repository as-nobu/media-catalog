"""Physical calibration from TIFF tags and embedded OME XML; no external reads."""
import math
import xml.etree.ElementTree as ET

UNITS = {'µm':1,'μm':1,'um':1,'nm':.001,'mm':1000,'cm':10000,'m':1000000,
         'pm':.000001,'Å':.0001,'in':25400}


def positive(value):
    try:
        value=float(value)
        return value if math.isfinite(value) and value>0 else None
    except (ValueError,TypeError,ZeroDivisionError,OverflowError):
        return None


def ome_scales(description, count):
    result={}
    if not description: return result
    if isinstance(description,bytes): description=description.decode('utf-8',errors='replace')
    if len(description)>4_000_000 or '<!DOCTYPE' in description or '<!ENTITY' in description: return result
    try:
        root=ET.fromstring(description)
        if root.tag.split('}')[-1]!='OME': return result
        for pixels in root.iter():
            if pixels.tag.split('}')[-1]!='Pixels': continue
            scale={}
            for axis in ('X','Y'):
                value=positive(pixels.get('PhysicalSize'+axis))
                factor=UNITS.get(pixels.get('PhysicalSize'+axis+'Unit','µm'))
                scale[axis.lower()]=positive(value*factor) if value and factor else None
            if not any(scale.values()): continue
            scale['source']='OME-TIFF'
            scale['size_x']=int(pixels.get('SizeX','0'))
            scale['size_y']=int(pixels.get('SizeY','0'))
            blocks=[e for e in pixels if e.tag.split('}')[-1]=='TiffData']
            for block in blocks:
                uuids=[e for e in block if e.tag.split('}')[-1]=='UUID']
                # Never follow other files; only the current file's UUID is eligible.
                if uuids and any(e.text!=root.get('UUID') for e in uuids): continue
                start=int(block.get('IFD','0'))
                planes=int(block.get('PlaneCount', '1' if 'IFD' in block.attrib else str(count)))
                for index in range(max(0,start),min(count,start+planes)):
                    if index in result: result[index]=None # ambiguous mapping
                    else: result[index]=dict(scale)
    except (ET.ParseError,ValueError,TypeError):
        return {}
    return result


def calibration(image, ome=None):
    tags=image.tag_v2
    unit={2:25400,3:10000}.get(tags.get(296,2))
    result={'x':None,'y':None,'source':'TIFF'}
    for axis,tag in (('x',282),('y',283)):
        resolution=positive(tags.get(tag))
        result[axis]=positive(unit/resolution) if unit and resolution else None
    if ome is not None and (ome.get('size_x'),ome.get('size_y')) == image.size: result=dict(ome) # physical OME calibration takes precedence
    result.update(width=image.width,height=image.height)
    if tags.get(274,1) in (5,6,7,8):
        result['x'],result['y']=result['y'],result['x']
        result['width'],result['height']=result['height'],result['width']
    return result


def nice_bar(scale, display_width):
    value=positive(scale.get('x'))
    width=positive(scale.get('width'))
    if not value or not width or display_width<80: return None
    field=value*width
    target=field*.25
    power=10**math.floor(math.log10(target))
    length=max(v*power for v in (1,2,5) if v*power<=target)
    pixels=display_width*length/field
    if length>=1000: label=f'{length/1000:g} mm'
    elif length<1: label=f'{length*1000:g} nm'
    else: label=f'{length:g} µm'
    return pixels,label
