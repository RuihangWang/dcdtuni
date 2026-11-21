/*---------------------------------------------------------------------------*\
  dcCartOctree — a sparse, level-balanced octree over a dcCartGrid.

  Only LEAVES are stored, per level: a bit per (i,j,k) of the level
  (dcCartLevelSet; a flat hash table of packed keys beyond 2^30 cells), plus
  the cells that were refined. There are no pointers and no per-cell
  allocation, so refining is "clear one bit, set eight" and the whole tree for
  a data hall of five million leaves is a few tens of MB.

  The tree is kept 2:1 balanced across faces (optionally with a wider buffer),
  which is what makes the mesh builder's job tractable: the neighbour of a leaf
  across a face is either one leaf at the same level, one leaf one level
  coarser, or exactly four leaves one level finer. Nothing else can occur.

  Deliberately NOT a general-purpose octree: there is no point location beyond
  what the grid provides, and no payload. Refinement decisions (which cells cut
  a surface, which lie inside a refinement region) belong to the caller.

\*---------------------------------------------------------------------------*/

#ifndef dcCartOctree_H
#define dcCartOctree_H

#include "dcCartGrid.H"
#include "labelList.H"
#include "DynamicList.H"
#include "error.H"
#include <unordered_set>
#include <cstdint>
#include <cstddef>
#include <iterator>
#include <vector>

namespace Foam
{

/*---------------------------------------------------------------------------*\
                        Class dcCartKeySet Declaration
\*---------------------------------------------------------------------------*/

//- A set of packed cell keys in one flat array: open addressing, linear
//  probing, deletion by shifting the rest of the run back (no tombstones).
//  std::unordered_set keeps a list node per key behind a bucket array, and the
//  level lookups of balancing and of every neighbour query chased those
//  pointers. A key is never ~0 (dcCartPack uses 63 bits), which marks a slot
//  empty.
class dcCartKeySet
{
public:

    static constexpr uint64_t emptyKey = ~uint64_t(0);

    class const_iterator
    {
        const uint64_t* p_;
        const uint64_t* end_;
        void skip() { while (p_ != end_ && *p_ == emptyKey) { ++p_; } }

    public:
        typedef std::forward_iterator_tag iterator_category;
        typedef uint64_t value_type;
        typedef std::ptrdiff_t difference_type;
        typedef const uint64_t* pointer;
        typedef const uint64_t& reference;

        const_iterator(const uint64_t* p, const uint64_t* e) : p_(p), end_(e) { skip(); }
        reference operator*() const { return *p_; }
        const_iterator& operator++() { ++p_; skip(); return *this; }
        const_iterator operator++(int) { const_iterator t(*this); ++*this; return t; }
        bool operator==(const const_iterator& o) const { return p_ == o.p_; }
        bool operator!=(const const_iterator& o) const { return p_ != o.p_; }
    };

private:

    std::vector<uint64_t> slots_;
    size_t size_ = 0;

    static size_t home(uint64_t k, size_t mask)
    {
        k ^= k >> 33;
        k *= 0xff51afd7ed558ccdULL;
        k ^= k >> 33;
        return size_t(k) & mask;
    }

    void rehash(size_t cap)
    {
        std::vector<uint64_t> old(cap, emptyKey);
        old.swap(slots_);
        const size_t mask = cap - 1;
        for (const uint64_t k : old)
        {
            if (k == emptyKey) { continue; }
            size_t i = home(k, mask);
            while (slots_[i] != emptyKey) { i = (i + 1) & mask; }
            slots_[i] = k;
        }
    }

public:

    size_t size() const { return size_; }
    bool empty() const { return size_ == 0; }

    void clear()
    {
        std::vector<uint64_t>().swap(slots_);
        size_ = 0;
    }

    void reserve(size_t n)
    {
        size_t cap = 16;
        while (cap < 2*n) { cap <<= 1; }
        if (cap > slots_.size()) { rehash(cap); }
    }

    bool insert(const uint64_t k)
    {
        if (2*(size_ + 1) > slots_.size())
        {
            rehash(slots_.empty() ? size_t(16) : 2*slots_.size());
        }
        const size_t mask = slots_.size() - 1;
        size_t i = home(k, mask);
        while (true)
        {
            const uint64_t s = slots_[i];
            if (s == k) { return false; }
            if (s == emptyKey) { slots_[i] = k; ++size_; return true; }
            i = (i + 1) & mask;
        }
    }

    size_t count(const uint64_t k) const
    {
        if (slots_.empty()) { return 0; }
        const size_t mask = slots_.size() - 1;
        size_t i = home(k, mask);
        while (true)
        {
            const uint64_t s = slots_[i];
            if (s == k) { return 1; }
            if (s == emptyKey) { return 0; }
            i = (i + 1) & mask;
        }
    }

    bool erase(const uint64_t k)
    {
        if (slots_.empty()) { return false; }
        const size_t mask = slots_.size() - 1;
        size_t i = home(k, mask);
        while (true)
        {
            const uint64_t s = slots_[i];
            if (s == emptyKey) { return false; }
            if (s == k) { break; }
            i = (i + 1) & mask;
        }
        size_t j = i;
        while (true)
        {
            j = (j + 1) & mask;
            const uint64_t s = slots_[j];
            if (s == emptyKey) { break; }
            const size_t hs = home(s, mask);
            // stays where it is when its home lies cyclically in (i, j]
            const bool stays = (i <= j) ? (i < hs && hs <= j) : (i < hs || hs <= j);
            if (!stays) { slots_[i] = s; i = j; }
        }
        slots_[i] = emptyKey;
        --size_;
        return true;
    }

    const_iterator begin() const
    {
        const uint64_t* b = slots_.data();
        return const_iterator(b, b + slots_.size());
    }

    const_iterator end() const
    {
        const uint64_t* e = slots_.data() + slots_.size();
        return const_iterator(e, e);
    }
};

/*---------------------------------------------------------------------------*\
                       Class dcCartLevelSet Declaration
\*---------------------------------------------------------------------------*/

//- The cells of one octree level: a bit per (i,j,k) of the level when its
//  index space fits in 2^30 bits, else a dcCartKeySet. The bitmap turns each
//  membership test into one bit read in spatial order — the neighbour and
//  containing-level queries of balancing, carving and assembly walk the
//  cells in space, and a hash scatters them. Iteration yields ascending keys.
class dcCartLevelSet
{
    label nx_ = 0, ny_ = 0, nz_ = 0;
    bool bitmap_ = false;
    std::vector<uint64_t> words_;
    size_t count_ = 0;
    dcCartKeySet hash_;

    bool indexOf(const uint64_t key, uint64_t& idx) const
    {
        const uint64_t i = (key >> (2*dcCartKeyBits)) & dcCartKeyMask;
        const uint64_t j = (key >> dcCartKeyBits) & dcCartKeyMask;
        const uint64_t k = key & dcCartKeyMask;
        if (i >= uint64_t(nx_) || j >= uint64_t(ny_) || k >= uint64_t(nz_)) { return false; }
        idx = (i*uint64_t(ny_) + j)*uint64_t(nz_) + k;
        return true;
    }

public:

    class const_iterator
    {
        const dcCartLevelSet* s_;
        bool hashMode_;
        dcCartKeySet::const_iterator h_;
        size_t w_ = 0;
        uint64_t bits_ = 0;
        uint64_t key_ = 0;
        bool end_ = true;

        void nextBit()
        {
            while (bits_ == 0)
            {
                if (++w_ >= s_->words_.size()) { end_ = true; return; }
                bits_ = s_->words_[w_];
            }
            const uint64_t b = uint64_t(__builtin_ctzll(bits_));
            bits_ &= bits_ - 1;
            uint64_t idx = (uint64_t(w_) << 6) | b;
            const uint64_t nz = uint64_t(s_->nz_), ny = uint64_t(s_->ny_);
            const uint64_t k = idx % nz;
            idx /= nz;
            const uint64_t j = idx % ny;
            const uint64_t i = idx / ny;
            key_ = (i << (2*dcCartKeyBits)) | (j << dcCartKeyBits) | k;
            end_ = false;
        }

    public:
        typedef std::forward_iterator_tag iterator_category;
        typedef uint64_t value_type;
        typedef std::ptrdiff_t difference_type;
        typedef const uint64_t* pointer;
        typedef const uint64_t& reference;

        const_iterator(const dcCartLevelSet* s, bool atEnd)
        :
            s_(s),
            hashMode_(!s->bitmap_),
            h_(atEnd ? s->hash_.end() : s->hash_.begin())
        {
            if (hashMode_ || atEnd || s->words_.empty()) { end_ = true; return; }
            w_ = 0;
            bits_ = s->words_[0];
            nextBit();
        }

        reference operator*() const { return hashMode_ ? *h_ : key_; }

        const_iterator& operator++()
        {
            if (hashMode_) { ++h_; } else { nextBit(); }
            return *this;
        }

        bool operator==(const const_iterator& o) const
        {
            if (hashMode_) { return h_ == o.h_; }
            if (end_ || o.end_) { return end_ == o.end_; }
            return w_ == o.w_ && bits_ == o.bits_;
        }
        bool operator!=(const const_iterator& o) const { return !(*this == o); }
    };

    void setShape(label nx, label ny, label nz)
    {
        clear();
        nx_ = nx; ny_ = ny; nz_ = nz;
        const uint64_t bits = uint64_t(nx)*uint64_t(ny)*uint64_t(nz);
        bitmap_ = (bits > 0 && bits <= (uint64_t(1) << 30));
    }

    size_t size() const { return bitmap_ ? count_ : hash_.size(); }
    bool empty() const { return size() == 0; }

    void clear()
    {
        std::vector<uint64_t>().swap(words_);
        count_ = 0;
        hash_.clear();
    }

    void reserve(size_t n) { if (!bitmap_) { hash_.reserve(n); } }

    bool insert(const uint64_t key)
    {
        if (!bitmap_) { return hash_.insert(key); }
        uint64_t idx = 0;
        if (!indexOf(key, idx))
        {
            FatalErrorInFunction
                << "cell key outside its level" << abort(FatalError);
        }
        if (words_.empty())
        {
            words_.assign((uint64_t(nx_)*uint64_t(ny_)*uint64_t(nz_) + 63)/64, 0);
        }
        uint64_t& w = words_[idx >> 6];
        const uint64_t m = uint64_t(1) << (idx & 63);
        if (w & m) { return false; }
        w |= m;
        ++count_;
        return true;
    }

    size_t count(const uint64_t key) const
    {
        if (!bitmap_) { return hash_.count(key); }
        uint64_t idx = 0;
        if (words_.empty() || !indexOf(key, idx)) { return 0; }
        return (words_[idx >> 6] >> (idx & 63)) & 1;
    }

    bool erase(const uint64_t key)
    {
        if (!bitmap_) { return hash_.erase(key); }
        uint64_t idx = 0;
        if (words_.empty() || !indexOf(key, idx)) { return false; }
        uint64_t& w = words_[idx >> 6];
        const uint64_t m = uint64_t(1) << (idx & 63);
        if (!(w & m)) { return false; }
        w &= ~m;
        --count_;
        return true;
    }

    const_iterator begin() const { return const_iterator(this, false); }
    const_iterator end() const { return const_iterator(this, true); }
};



class dcCartOctree
{
public:

    //- What sits across one face of a leaf
    enum neighbourType
    {
        OUTSIDE,   //!< index falls off the domain
        SAME,      //!< one leaf at the same level
        COARSER,   //!< one leaf exactly one level coarser
        FINER      //!< four leaves exactly one level finer
    };


private:

    // Private data

        const dcCartGrid& grid_;

        //- Hard ceiling on refinement depth
        label maxLevel_;

        //- leaves_[L] holds the packed keys of every leaf at level L
        std::vector<dcCartLevelSet> leaves_;

        //- internal_[L]: every level-L cell that was refined (it holds no
        //  leaf, and neither does anything above it)
        std::vector<dcCartLevelSet> internal_;


    // Private Member Functions

        //- Grow leaves_ so that index `level` is addressable
        void ensureLevel(label level);

        //- True when (level, idx) addresses a cell inside the domain
        inline bool inRange(label level, const labelVector& idx) const
        {
            for (direction cmpt = 0; cmpt < 3; ++cmpt)
            {
                if (idx[cmpt] < 0 || idx[cmpt] >= grid_.nAtLevel(cmpt, level))
                {
                    return false;
                }
            }
            return true;
        }


public:

    // Constructors

        //- Empty tree over `grid`, refinable to `maxLevel`
        dcCartOctree(const dcCartGrid& grid, label maxLevel);


    // Member Functions

        // Access

            inline const dcCartGrid& grid() const { return grid_; }
            inline label maxLevel() const { return maxLevel_; }

            //- Deepest level that currently holds any leaf
            label deepestLevel() const;

            //- Leaf count over all levels
            label nLeaves() const;

            //- Leaf count at one level
            label nLeavesAt(label level) const;

            //- The leaf keys at one level (empty when level is out of range)
            const dcCartLevelSet& leavesAt(label level) const;

            //- True when (level, idx) is itself a leaf
            bool isLeaf(label level, const labelVector& idx) const;


        // Construction

            //- Fill the tree with every background cell at `level`
            void seed(label level = 0);

            //- Split the given leaves into eight children each.
            //  Entries already at maxLevel(), or that are not leaves, are
            //  skipped. Returns the number actually refined.
            label refine(label level, const List<labelVector>& idx);

            //- Convenience: refine by packed key
            label refineKeys(label level, const List<uint64_t>& keys);

            //- Enforce 2:1 balance across faces.
            //  `nBuffer` extra cells of the finer level are inserted around
            //  every level jump (snappy's nCellsBetweenLevels). Returns the
            //  number of cells refined.
            label balance(label nBuffer = 1);


        // Queries

            //- Level of the leaf containing (level, idx), walking coarser.
            //  Returns -1 when nothing covers it (only possible mid-construction).
            label containingLevel(label level, const labelVector& idx) const;

            //- Classify what lies across face (cmpt, positive) of leaf
            //  (level, idx). On COARSER/SAME `nbrIdx` and `nbrLevel` name the
            //  neighbour leaf; on FINER they name the level-(level+1) block the
            //  four finer leaves live in (use finerFaceNeighbours to list them).
            neighbourType neighbour
            (
                label level,
                const labelVector& idx,
                direction cmpt,
                bool positive,
                label& nbrLevel,
                labelVector& nbrIdx
            ) const;

            //- The four level-(level+1) leaves across face (cmpt, positive)
            void finerFaceNeighbours
            (
                label level,
                const labelVector& idx,
                direction cmpt,
                bool positive,
                FixedList<labelVector, 4>& out
            ) const;

            //- Flatten every leaf into parallel (level, index) arrays, ordered
            //  coarse-to-fine and by key within a level, so a run is reproducible.
            void flatten(labelList& levels, List<labelVector>& indices) const;


        // Info

            string info() const;
};


} // End namespace Foam

#endif
