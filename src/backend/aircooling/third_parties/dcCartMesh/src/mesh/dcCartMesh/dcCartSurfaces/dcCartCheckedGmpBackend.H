#ifndef dcCartCheckedGmpBackend_H
#define dcCartCheckedGmpBackend_H
// PRIVATE arithmetic prototype. Value bound is checked before publication.
// No inheritance from gmp_int; no mutable native handle; no production ABI claim.
#include <boost/multiprecision/gmp.hpp>
#include <boost/multiprecision/cpp_int.hpp>
#include <boost/mpl/list.hpp>
#include <boost/functional/hash.hpp>
#include <limits>
#include <type_traits>
#include <stdexcept>
#include <string>
#include <cstring>
#include <cmath>
#include <cstdint>
namespace dcCartCheckedGmp {
class CheckedGmpBackend {
    boost::multiprecision::backends::gmp_int value_;
    static void check(mpz_srcptr p) {
        if(mpz_sgn(p)&&mpz_sizeinbase(p,2)>16384)
            throw std::overflow_error("checked GMP 16384-bit overflow");
    }
public:
    typedef boost::mpl::list<long,long long> signed_types;
    typedef boost::mpl::list<unsigned long,unsigned long long> unsigned_types;
    typedef boost::mpl::list<double,long double> float_types;
    static constexpr unsigned max_bits=16384;
    CheckedGmpBackend()=default;
    CheckedGmpBackend(const CheckedGmpBackend&)=default;
    CheckedGmpBackend(CheckedGmpBackend&& other) noexcept : value_() {value_.swap(other.value_);}
    CheckedGmpBackend& operator=(const CheckedGmpBackend&)=default;
    CheckedGmpBackend& operator=(CheckedGmpBackend&& other) noexcept {value_.swap(other.value_);return *this;}
    mpz_srcptr read()const {return value_.data();}
    template<class F> void update(F operation) {
        // Inputs already <=16384; multiplication scratch <=32768, never published.
        boost::multiprecision::backends::gmp_int temporary;
        operation(temporary.data());check(temporary.data());value_.swap(temporary);
    }
    template<class T,typename std::enable_if<std::is_integral<T>::value,int>::type=0>
    CheckedGmpBackend& operator=(T n) {
        static_assert(sizeof(T)<=sizeof(unsigned long),"private prototype supports native <=64-bit integers");
        update([&](mpz_ptr p){
            if(std::is_signed<T>::value)mpz_set_si(p,static_cast<long>(n));
            else mpz_set_ui(p,static_cast<unsigned long>(n));
        });return *this;
    }
    CheckedGmpBackend& operator=(const char* text) {
        if(!text)throw std::runtime_error("null integer string");
        const char* p=text;bool negative=false;
        // Preserve cpp_int's native syntax: '-' is supported, '+' is not.
        if(*p=='-'){negative=true;++p;}
        unsigned base=10;
        if(*p=='0'){
            if(p[1]=='x'||p[1]=='X'){base=16;p+=2;}
            else base=8;
        }
        const char* significant=nullptr;std::size_t digits=0;
        for(const char* q=p;*q;++q){
            const char c=*q;const unsigned value=c>='0'&&c<='9'?unsigned(c-'0'):
              c>='a'&&c<='f'?unsigned(c-'a'+10):c>='A'&&c<='F'?unsigned(c-'A'+10):99;
            if(value>=base)throw std::runtime_error("invalid integer digit");
            if(value||significant){if(!significant)significant=q;++digits;}
        }
        if(digits>(base==16?4096u:base==8?5462u:4933u))
            throw std::overflow_error("checked GMP parse bound");
        // No unbounded temporary string; insignificant leading zeroes are scanned.
        // Empty / '-' / '0x' are legacy cpp_int zero syntax.
        const std::string bounded=significant?std::string(significant,digits):"0";
        update([&](mpz_ptr z){if(mpz_set_str(z,bounded.c_str(),base))throw std::runtime_error("integer parse");if(negative)mpz_neg(z,z);});
        return *this;
    }
    template<class T,typename std::enable_if<std::is_floating_point<T>::value,int>::type=0>
    CheckedGmpBackend& operator=(T x) {
        // Cold compatibility fallback uses the existing checked conversion policy.
        using Ref=boost::multiprecision::number<boost::multiprecision::cpp_int_backend<
          0,16384,boost::multiprecision::signed_magnitude,boost::multiprecision::checked>>;
        if(!std::isfinite(x))throw std::runtime_error("nonfinite integer assignment");
        Ref ref(x);return operator=(ref.str().c_str());
    }
    template<class T,typename std::enable_if<std::is_arithmetic<T>::value,int>::type=0>
    CheckedGmpBackend(T n){*this=n;}
    explicit CheckedGmpBackend(const char* s){*this=s;}
    void swap(CheckedGmpBackend& b){value_.swap(b.value_);}
    void negate(){mpz_neg(value_.data(),value_.data());}
    int compare(const CheckedGmpBackend& b)const{return mpz_cmp(read(),b.read());}
    template<class T> int compare(T n)const {CheckedGmpBackend b(n);return compare(b);}
    std::string str(std::streamsize digits,std::ios_base::fmtflags flags)const{return value_.str(digits,flags);}
};
using B=CheckedGmpBackend;
inline bool eval_is_zero(const B&a){return mpz_sgn(a.read())==0;}
inline int eval_get_sign(const B&a){return mpz_sgn(a.read());}
inline void eval_abs(B&r,const B&a){r.update([&](mpz_ptr p){mpz_abs(p,a.read());});}
#define DCCG_BINARY(NAME,FN) \
inline void NAME(B&r,const B&a,const B&b){r.update([&](mpz_ptr p){FN(p,a.read(),b.read());});} \
inline void NAME(B&r,const B&b){NAME(r,r,b);}
DCCG_BINARY(eval_add,mpz_add)
DCCG_BINARY(eval_subtract,mpz_sub)
DCCG_BINARY(eval_multiply,mpz_mul)
DCCG_BINARY(eval_gcd,mpz_gcd)
DCCG_BINARY(eval_lcm,mpz_lcm)
#undef DCCG_BINARY
inline void eval_divide(B&r,const B&a,const B&b){if(eval_is_zero(b))throw std::overflow_error("Division by zero.");r.update([&](mpz_ptr p){mpz_tdiv_q(p,a.read(),b.read());});}
inline void eval_modulus(B&r,const B&a,const B&b){if(eval_is_zero(b))throw std::overflow_error("Division by zero.");r.update([&](mpz_ptr p){mpz_tdiv_r(p,a.read(),b.read());});}
inline void eval_divide(B&r,const B&b){eval_divide(r,r,b);}
inline void eval_modulus(B&r,const B&b){eval_modulus(r,r,b);}
#define DCCG_SCALAR(NAME) \
template<class T,typename std::enable_if<std::is_integral<T>::value,int>::type=0> \
inline void NAME(B&r,T b){B t(b);NAME(r,t);} \
template<class T,typename std::enable_if<std::is_integral<T>::value,int>::type=0> \
inline void NAME(B&r,const B&a,T b){B t(b);NAME(r,a,t);}
DCCG_SCALAR(eval_add) DCCG_SCALAR(eval_subtract) DCCG_SCALAR(eval_multiply)
DCCG_SCALAR(eval_divide) DCCG_SCALAR(eval_modulus)
#undef DCCG_SCALAR
inline void eval_increment(B&r){eval_add(r,1);}
inline void eval_decrement(B&r){eval_subtract(r,1);}
inline void positiveBits(const B&a){if(eval_get_sign(a)<0)throw std::range_error("Bitwise operations on negative values results in undefined behavior.");}
template<class T>inline void eval_left_shift(B&r,const B&a,T n){
    positiveBits(a);if(std::is_signed<T>::value&&n<0)throw std::out_of_range("negative shift");
    if(eval_is_zero(a)){r=0;return;}
    const auto count=static_cast<std::uint64_t>(n);
    if(count>=16384||mpz_sizeinbase(a.read(),2)+count>16384)throw std::overflow_error("checked GMP shift");
    r.update([&](mpz_ptr p){mpz_mul_2exp(p,a.read(),static_cast<mp_bitcnt_t>(count));});
}
template<class T>inline void eval_left_shift(B&r,T n){eval_left_shift(r,r,n);}
template<class T>inline void eval_right_shift(B&r,const B&a,T n){
    positiveBits(a);if(std::is_signed<T>::value&&n<0)throw std::out_of_range("negative shift");
    const auto count=static_cast<std::uint64_t>(n);
    if(count>=16384){r=0;return;}
    r.update([&](mpz_ptr p){mpz_tdiv_q_2exp(p,a.read(),static_cast<mp_bitcnt_t>(count));});
}
template<class T>inline void eval_right_shift(B&r,T n){eval_right_shift(r,r,n);}
#define DCCG_BIT(NAME,FN) \
inline void NAME(B&r,const B&a,const B&b){positiveBits(a);positiveBits(b);r.update([&](mpz_ptr p){FN(p,a.read(),b.read());});} \
inline void NAME(B&r,const B&b){NAME(r,r,b);}
DCCG_BIT(eval_bitwise_and,mpz_and)
DCCG_BIT(eval_bitwise_or,mpz_ior)
DCCG_BIT(eval_bitwise_xor,mpz_xor)
#undef DCCG_BIT
// Old signed checked backend cannot compile ~; preserve that prohibition.
void eval_complement(B&,const B&)=delete;
inline unsigned eval_msb(const B&a){positiveBits(a);if(eval_is_zero(a))throw std::range_error("No bits were set.");return static_cast<unsigned>(mpz_sizeinbase(a.read(),2)-1);}
inline unsigned eval_lsb(const B&a){positiveBits(a);if(eval_is_zero(a))throw std::range_error("No bits were set.");return static_cast<unsigned>(mpz_scan1(a.read(),0));}
inline bool eval_bit_test(const B&a,unsigned n){
    if(n>=16384)return false;
    if(eval_get_sign(a)>=0)return mpz_tstbit(a.read(),n)!=0;
    B mag;eval_abs(mag,a);return mpz_tstbit(mag.read(),n)!=0;
}
inline void eval_bit_set(B&a,unsigned n){
    if(n>=16384)throw std::overflow_error("checked GMP bit set");
    const bool neg=eval_get_sign(a)<0;
    a.update([&](mpz_ptr p){mpz_abs(p,a.read());mpz_setbit(p,n);if(neg)mpz_neg(p,p);});
}
inline void eval_bit_unset(B&a,unsigned n){
    if(n>=16384)return;const bool neg=eval_get_sign(a)<0;
    a.update([&](mpz_ptr p){mpz_abs(p,a.read());mpz_clrbit(p,n);if(neg)mpz_neg(p,p);});
}
inline void eval_bit_flip(B&a,unsigned n){
    if(n>=16384)throw std::overflow_error("checked GMP bit flip");const bool neg=eval_get_sign(a)<0;
    a.update([&](mpz_ptr p){mpz_abs(p,a.read());mpz_combit(p,n);if(neg)mpz_neg(p,p);});
}
template<class T> inline void eval_convert_to(T*out,const B&a){
    // Fast native conversion only after the entire value fits the target range.
    // All non-native, out-of-range and floating conversions keep the old policy.
    if constexpr(std::is_integral<T>::value&&sizeof(T)<=sizeof(unsigned long)){
        if constexpr(std::is_signed<T>::value){
            if(mpz_cmp_si(a.read(),static_cast<long>((std::numeric_limits<T>::min)()))>=0
              &&mpz_cmp_si(a.read(),static_cast<long>((std::numeric_limits<T>::max)()))<=0){
                *out=static_cast<T>(mpz_get_si(a.read()));return;
            }
        } else {
            if(eval_get_sign(a)>=0&&mpz_cmp_ui(a.read(),static_cast<unsigned long>((std::numeric_limits<T>::max)()))<=0){
                *out=static_cast<T>(mpz_get_ui(a.read()));return;
            }
        }
    }
    // Existing cpp_int checked narrowing/FP-conversion behavior, not GMP truncation.
    using Ref=boost::multiprecision::number<boost::multiprecision::cpp_int_backend<
      0,16384,boost::multiprecision::signed_magnitude,boost::multiprecision::checked>>;
    Ref v(a.str(0,std::ios_base::fmtflags(0)));*out=v.template convert_to<T>();
}
inline std::size_t hash_value(const B&a){
    // Magnitude canonical 64-bit limbs and sign; equal integers have equal hashes.
    std::uint64_t limbs[256]{};std::size_t count=0;
    mpz_export(limbs,&count,-1,sizeof(std::uint64_t),0,0,a.read());
    std::size_t h=0;for(std::size_t j=0;j<count;++j)boost::hash_combine(h,limbs[j]);
    boost::hash_combine(h,eval_get_sign(a)<0);return h;
}
} // dcCartCheckedGmp
namespace boost { namespace multiprecision {
template<>struct number_category<dcCartCheckedGmp::CheckedGmpBackend>:boost::mpl::int_<number_kind_integer>{};
template<>struct is_unsigned_number<dcCartCheckedGmp::CheckedGmpBackend>:boost::mpl::false_{};
template<>struct is_signed_number<dcCartCheckedGmp::CheckedGmpBackend>:boost::mpl::true_{};
using CheckedGmpInteger=number<dcCartCheckedGmp::CheckedGmpBackend,et_off>;
}}
namespace std {
template<>class numeric_limits<boost::multiprecision::CheckedGmpInteger>{
    using T=boost::multiprecision::CheckedGmpInteger;
public:
    static constexpr bool is_specialized=true,is_signed=true,is_integer=true,is_exact=true,is_bounded=true,is_modulo=false,traps=false;
    static constexpr int digits=16384,digits10=(16384*301)/1000,max_digits10=digits10+3,radix=2,min_exponent=0,min_exponent10=0,max_exponent=0,max_exponent10=0;
    static constexpr bool has_infinity=false,has_quiet_NaN=false,has_signaling_NaN=false,has_denorm_loss=false,is_iec559=false,tinyness_before=false;
    static constexpr float_denorm_style has_denorm=denorm_absent;
    static constexpr float_round_style round_style=round_toward_zero;
    static T max(){return ((T(1)<<16383)-1)+(T(1)<<16383);}
    static T min(){return -max();}
    static T lowest(){return min();}
    static T epsilon(){return 0;}static T round_error(){return 0;}
    static T infinity(){return 0;}static T quiet_NaN(){return 0;}static T signaling_NaN(){return 0;}static T denorm_min(){return 0;}
};
}
namespace boost { namespace multiprecision {
template<class OutputIterator>
OutputIterator export_bits(const CheckedGmpInteger& v,OutputIterator out,unsigned chunk_size,bool msv_first=true){
    if(chunk_size==0||chunk_size>64)throw std::invalid_argument("private export chunk size");
    CheckedGmpInteger t=v<0?-v:v;std::vector<std::uint64_t> words;
    if(t==0)words.push_back(0);
    else {
        const auto mask=chunk_size==64?~std::uint64_t(0):(std::uint64_t(1)<<chunk_size)-1;
        while(t!=0){std::uint64_t limb=static_cast<std::uint64_t>(mpz_get_ui(t.backend().read()));words.push_back(limb&mask);t>>=chunk_size;}
    }
    if(msv_first)for(auto i=words.rbegin();i!=words.rend();++i)*out++=*i;
    else for(auto x:words)*out++=x;
    return out;
}
template<class Iterator>
CheckedGmpInteger& import_bits(CheckedGmpInteger& v,Iterator first,Iterator last,unsigned chunk_size=0,bool msv_first=true){
    using Value=typename std::iterator_traits<Iterator>::value_type;
    if(chunk_size==0)chunk_size=std::numeric_limits<Value>::digits;
    if(chunk_size==0||chunk_size>64)throw std::invalid_argument("private import chunk size");
    const std::size_t capacity=(16384+chunk_size-1)/chunk_size;
    CheckedGmpInteger temporary=0;
    auto limb=[&](Iterator it){
        if(std::is_signed<Value>::value&&*it<0)throw std::range_error("negative import limb");
        const auto n=static_cast<std::uint64_t>(*it);
        if(chunk_size<64&&(n>>chunk_size))throw std::range_error("oversized import limb");
        return n;
    };
    if(msv_first){
        // Leading zero words do not expand the represented value or scratch.
        for(;first!=last;++first){const auto n=limb(first);temporary<<=chunk_size;temporary+=n;}
    } else {
        // Keep at most the representable low words; extra high zeroes are valid.
        std::vector<std::uint64_t> words;words.reserve(capacity);
        for(;first!=last;++first){
            const auto n=limb(first);
            if(words.size()<capacity)words.push_back(n);
            else if(n)throw std::overflow_error("checked GMP import bound");
        }
        while(!words.empty()&&!words.back())words.pop_back();
        for(auto i=words.rbegin();i!=words.rend();++i){temporary<<=chunk_size;temporary+=*i;}
    }
    v=std::move(temporary);return v;
}
}} // boost::multiprecision

#endif
