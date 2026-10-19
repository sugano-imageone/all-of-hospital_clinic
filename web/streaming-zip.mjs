/* ZIP32 writer using native streaming DEFLATE; bounded XML buffers for XLSX. */
const encoder=new TextEncoder();
const crcTable=Uint32Array.from({length:256},(_,n)=>{
 for(let k=0;k<8;k++)n=n&1?0xedb88320^(n>>>1):n>>>1;
 return n>>>0;
});
function crcUpdate(crc,bytes) {for(const b of bytes)crc=crcTable[(crc^b)&255]^(crc>>>8);return crc;}
function header(length,signature) {
 const bytes=new Uint8Array(length),view=new DataView(bytes.buffer);view.setUint32(0,signature,true);
 return {bytes,u16:(at,v)=>view.setUint16(at,v,true),u32:(at,v)=>view.setUint32(at,v,true)};
}
export async function streamingZip(members) {
 const local=[],central=[];let offset=0,centralSize=0,count=0;
 for(const member of members) {
  const name=encoder.encode(member.name),stream=new CompressionStream('deflate-raw');
  const compressedPromise=new Response(stream.readable).arrayBuffer();
  const writer=stream.writable.getWriter();let crc=0xffffffff,size=0;
  try {
   for await(const part of member.chunks()) {
    const bytes=typeof part==='string'?encoder.encode(part):part;
    size+=bytes.length;crc=crcUpdate(crc,bytes);await writer.write(bytes);
   }
   await writer.close();
  } catch(e) {await writer.abort(e).catch(()=>{});await compressedPromise.catch(()=>{});throw e;}
  const zipped=new Uint8Array(await compressedPromise);crc=(crc^0xffffffff)>>>0;
  if(size>=0xffffffff||zipped.length>=0xffffffff||offset>=0xffffffff||name.length>65535)throw Error('Excel exceeds ZIP32 limits');
  const h=header(30,0x04034b50);h.u16(4,20);h.u16(6,0x800);h.u16(8,8);h.u16(12,33);h.u32(14,crc);h.u32(18,zipped.length);h.u32(22,size);h.u16(26,name.length);
  local.push(h.bytes,name,zipped);
  const d=header(46,0x02014b50);d.u16(4,20);d.u16(6,20);d.u16(8,0x800);d.u16(10,8);d.u16(14,33);d.u32(16,crc);d.u32(20,zipped.length);d.u32(24,size);d.u16(28,name.length);d.u32(42,offset);
  central.push(d.bytes,name);centralSize+=46+name.length;offset+=30+name.length+zipped.length;count++;
 }
 if(count>65535||offset+centralSize>=0xffffffff)throw Error('Excel exceeds ZIP32 limits');
 const end=header(22,0x06054b50);end.u16(8,count);end.u16(10,count);end.u32(12,centralSize);end.u32(16,offset);
 const output=new Uint8Array(offset+centralSize+22);let n=0;
 for(const bytes of [...local,...central,end.bytes]){output.set(bytes,n);n+=bytes.length;}
 return output;
}
