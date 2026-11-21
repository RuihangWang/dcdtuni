"""Information loss (ported from the frozen tests/test_information_loss.py)."""
import itertools,unittest
from types import SimpleNamespace
import torch
from agent.policies.losses import information_policy_loss, joint_policy_loss

def sample():
    theta=torch.zeros(3,dtype=torch.float64,requires_grad=True)
    ds=torch.tensor([[.3,-.1,.2],[-.2,.4,-.1]],dtype=torch.float64)
    dx=torch.tensor([[.7,.2,-.3],[-.5,.1,.6]],dtype=torch.float64)
    jac=torch.tensor([[[.2,.1,0.]],[[.1,-.2,.3]]],dtype=torch.float64)
    s=SimpleNamespace(actions=torch.tensor([0,1]),
        values=torch.tensor([[.5],[.7]],dtype=torch.float64)+torch.einsum("nkp,p->nk",jac,theta),
        discrete_log_prob=ds@theta,continuous_log_prob=dx@theta)
    s.log_prob=s.discrete_log_prob+s.continuous_log_prob
    return s,theta,ds,dx,jac

class InformationLossTests(unittest.TestCase):
    def test_identical_auxiliary_reduces_to_executed_v2(self):
        for draw in itertools.product((False,True),repeat=2):
            s,t,*_=sample();mask=torch.tensor(draw);hf=torch.tensor([.8,-.2],dtype=torch.float64)
            hf[~mask]=float("nan");low=torch.tensor([.3,.1],dtype=torch.float64)
            gradient=torch.tensor([[.4],[-.2]],dtype=torch.float64)
            new,_=information_policy_loss(s,hf_rewards=hf,auxiliary_rewards=low,lf_rewards=low,
                selected=mask,probabilities=[.25,.7],value_coefficient=.6,path_coefficient=.6,lf_gradients=gradient)
            old,_=joint_policy_loss(s,method="continuous-lax",hf_rewards=hf,lf_rewards=low,
                selected=mask,probabilities=[.25,.7],coefficient=.6,proxy_gradients=gradient)
            gn=torch.autograd.grad(new,t,retain_graph=True)[0];go=torch.autograd.grad(old,t)[0]
            torch.testing.assert_close(gn,go)

    def test_different_value_proxy_has_correct_conditional_purchase_mean(self):
        high=torch.tensor([.8,-.2],dtype=torch.float64);low=torch.tensor([.3,.1],dtype=torch.float64)
        auxiliary=torch.tensor([.73,-.12],dtype=torch.float64)
        q=torch.tensor([.25,.7],dtype=torch.float64);gradient=torch.tensor([[.4],[-.2]],dtype=torch.float64)
        average=torch.zeros(3,dtype=torch.float64)
        for draw in itertools.product((False,True),repeat=2):
            s,t,*_=sample();mask=torch.tensor(draw);observed=high.clone();observed[~mask]=float("nan")
            loss,_=information_policy_loss(s,hf_rewards=observed,auxiliary_rewards=auxiliary,lf_rewards=low,
                selected=mask,probabilities=q,value_coefficient=.9,path_coefficient=.6,lf_gradients=gradient)
            average+=torch.where(mask,q,1-q).prod()*torch.autograd.grad(loss,t)[0]
        s,t,ds,dx,jac=sample()
        expected=-((high-high.flip(0))[:,None]*ds).mean(0)
        term=high-.6*low
        expected-=((term-term.flip(0))[:,None]*dx).mean(0)
        expected-=.6*(gradient[:,:,None]*jac).sum(1).mean(0)
        torch.testing.assert_close(average,expected)

    def test_known_function_outside_acquisition_for_any_coefficients(self):
        known=torch.tensor([.05,.07],dtype=torch.float64);gradient=torch.tensor([[.1],[.2]],dtype=torch.float64)
        expected=None
        for beta,alpha in itertools.product((-.4,0.,1.3),(0.,.7,1.)):
            for draw in itertools.product((False,True),repeat=2):
                s,t,*_=sample();mask=torch.tensor(draw);hf=known.clone();hf[~mask]=float("nan")
                loss,_=information_policy_loss(s,hf_rewards=hf,auxiliary_rewards=known,lf_rewards=known,
                    selected=mask,probabilities=[.2,.7],value_coefficient=beta,path_coefficient=alpha,
                    lf_gradients=gradient,known_rewards=known,known_gradients=gradient)
                actual=torch.autograd.grad(loss,t)[0]
                if expected is None:expected=actual
                else:torch.testing.assert_close(actual,expected)

    def test_scalar_control_does_not_request_or_invent_path_derivative(self):
        s,t,*_=sample()
        loss,_=information_policy_loss(s,hf_rewards=[-1e6,float("nan")],auxiliary_rewards=[.1,.2],
            lf_rewards=[.05,.1],selected=[True,False],probabilities=[.2,.7],path_coefficient=0.)
        self.assertTrue(torch.isfinite(torch.autograd.grad(loss,t)[0]).all())
        s,t,*_=sample()
        with self.assertRaises(ValueError):
            information_policy_loss(s,hf_rewards=[.1,.2],auxiliary_rewards=[.1,.2],
                lf_rewards=[.1,.2],selected=[True,True],probabilities=[1.,1.],lf_gradients=None)

if __name__=="__main__":unittest.main()
