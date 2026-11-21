/** One FIFO stage-mutation lane for every result owner on one OV transport.
 * Ownership/scene guards are checked after earlier mutations have ACKed, not
 * before entering another independent queue. In-flight writes finish before a
 * newer author; cancellation never claims to undo a command already sent. */
const lanes=new WeakMap<object,{tail:Promise<void>}>()
export function enqueueOvResultMutation<T>(client:object,isCurrent:()=>boolean,apply:()=>Promise<T>):Promise<T|undefined> {
  let lane=lanes.get(client)
  if(!lane){lane={tail:Promise.resolve()};lanes.set(client,lane)}
  const result=lane.tail.then(()=>isCurrent()?apply():undefined)
  lane.tail=result.then(()=>{},()=>{})
  return result
}
