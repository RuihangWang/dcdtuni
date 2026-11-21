#ifndef dcCartCompleteCavityReplaySourceSidecar_H
#define dcCartCompleteCavityReplaySourceSidecar_H
#include "dcCartFluidOriginSnapshotCore.H"
#include <cstdlib>
#include <memory>
// Observation-only const reference to the ALREADY constructed/verified producer.
// No authority/Store/Record/journal ABI extension, no extra decision callback.
namespace dcCartCompleteCavityReplaySourceSidecar
{
using Snapshot=dcCartFluidOriginSnapshotCore::Snapshot;
struct Reference {std::shared_ptr<const Snapshot> snapshot;int rank=-1;};
// Single library-owned slot: APP producer and dcCart library consumer share
// the same reference, independent of inline-local TLS/DSO coalescing.
extern thread_local Reference state;
inline Reference& reference()noexcept{return state;}
inline bool enabled()noexcept
{const char* v=std::getenv("DCCART_COMPLETE_REPLAY_OBSERVATION");return v&&v[0]=='1'&&!v[1];}
inline void resetIfEnabled()noexcept{if(enabled())reference()=Reference{};}
inline void bindIfEnabled(const std::shared_ptr<const Snapshot>& snapshot,int rank)noexcept
{if(enabled()){reference().snapshot=snapshot;reference().rank=rank;}}
}
#endif
