#ifndef dcCartBoostBoundedMultiply_H
#define dcCartBoostBoundedMultiply_H
#include <boost/version.hpp>
#include <boost/multiprecision/cpp_int.hpp>
// Boost 1.74's fixed-precision Karatsuba alias does not copy the normalized
// limb count back to its owner. Backport the result.resize(t.size(),t.size())
// used upstream since Boost 1.77. No change to precision or checked overflow.
// Based on Boost.Multiprecision, Copyright 2012-20 John Maddock,
// 2019-20 Christopher Kormanyos and Madhur Chauhan; Boost Software License 1.0.
#if BOOST_VERSION == 107400
namespace dcCartBoostBoundedMultiply
{
template<class Backend> inline void multiply
(Backend& result,const Backend& a,const Backend& b)
{
    namespace mp=boost::multiprecision;
    namespace be=boost::multiprecision::backends;
    using Variable=be::cpp_int_backend<0,0,mp::signed_magnitude,mp::unchecked,
        std::allocator<mp::limb_type>>;
    Variable av(a.limbs(),0,a.size()),bv(b.limbs(),0,b.size());
    const unsigned size=a.size()+b.size();
    const unsigned scratch=be::karatsuba_storage_size((std::max)(a.size(),b.size()));
    if(size*sizeof(mp::limb_type)*CHAR_BIT<=16384)
    {
        result.resize(size,size);
        Variable value(result.limbs(),0,result.size());
        Variable::scoped_shared_storage storage(value.allocator(),scratch);
        be::multiply_karatsuba(value,av,bv,storage);
        result.resize(value.size(),value.size());
    }
    else
    {
        Variable::scoped_shared_storage storage(Variable::allocator_type(),size+scratch);
        Variable value(storage,size);
        be::multiply_karatsuba(value,av,bv,storage);
        result=value; // checked backend refuses any result exceeding 16384 bits.
    }
}
}
namespace boost { namespace multiprecision { namespace backends
{
using dcCartDynamicBoundedBackend=cpp_int_backend<0,16384,signed_magnitude,checked,std::allocator<limb_type>>;
using dcCartStaticBoundedBackend=cpp_int_backend<16384,16384,signed_magnitude,checked,void>;
using dcCartInline512BoundedBackend=cpp_int_backend<512,16384,signed_magnitude,checked,std::allocator<limb_type>>;
template<> inline void setup_karatsuba
(dcCartInline512BoundedBackend& r,const dcCartInline512BoundedBackend& a,const dcCartInline512BoundedBackend& b)
{dcCartBoostBoundedMultiply::multiply(r,a,b);}
template<> inline void setup_karatsuba
(dcCartDynamicBoundedBackend& r,const dcCartDynamicBoundedBackend& a,const dcCartDynamicBoundedBackend& b)
{dcCartBoostBoundedMultiply::multiply(r,a,b);}
template<> inline void setup_karatsuba
(dcCartStaticBoundedBackend& r,const dcCartStaticBoundedBackend& a,const dcCartStaticBoundedBackend& b)
{dcCartBoostBoundedMultiply::multiply(r,a,b);}
}}}
#elif BOOST_VERSION < 107700
#error "dcCart exact geometry requires Boost 1.74 with its compatibility fix, or Boost 1.77+"
#endif
#endif
