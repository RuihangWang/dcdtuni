"""Bounded numerical head (ported from the frozen tests/test_numeric_policy.py)."""
import unittest
import torch
from agent.policies.numeric import NumericalDesignHead,categorical_kl,enumerated_continuous_kl

class NumericalHeadTests(unittest.TestCase):
    def setUp(self):
        self.head=NumericalDesignHead(3,[(.06,.22),(.25,.75)]).double()
        self.hidden=torch.tensor([[.3,-.2,.7]],dtype=torch.float64,requires_grad=True)
        self.logits=torch.tensor([[.2,-.1]],dtype=torch.float64,requires_grad=True)

    def test_valid_joint_support_and_density_roundtrip(self):
        sample=self.head.sample(self.hidden,self.logits,samples=12,generator=torch.Generator().manual_seed(4))
        self.assertTrue(((sample.values>self.head.lower)&(sample.values<self.head.lower+self.head.width)).all())
        expanded=self.hidden.expand(12,-1)
        torch.testing.assert_close(self.head.log_density(expanded,sample.actions,sample.values.detach()),sample.continuous_log_prob)
        self.assertTrue(torch.isfinite(sample.log_prob).all())

    def test_score_and_pathwise_have_distinct_correct_derivatives(self):
        sample=self.head.sample(self.hidden,self.logits,samples=5,generator=torch.Generator().manual_seed(8))
        score=torch.autograd.grad(sample.continuous_log_prob.sum(),self.head.mean_layer.bias,retain_graph=True)[0]
        raw_features=torch.cat([self.hidden.expand(5,-1),torch.nn.functional.one_hot(sample.actions,2).double()],-1)
        raw_mean=self.head.mean_layer(raw_features).detach()
        factor=1-torch.tanh(raw_mean/6)**2
        analytic=(((sample.latent.detach()-sample.mean.detach())/sample.std.detach()**2)*factor).sum(0)
        torch.testing.assert_close(score,analytic)
        path=torch.autograd.grad(sample.values.sum(),self.head.mean_layer.bias,retain_graph=True)[0]
        expected=(self.head.width*sample.latent.sigmoid()*(1-sample.latent.sigmoid())*factor).sum(0)
        torch.testing.assert_close(path,expected)
        self.assertGreater((score-path).abs().sum().item(),.01)

    def test_discrete_score_independent_of_continuous_draw(self):
        sample=self.head.sample(self.hidden,self.logits,samples=20,generator=torch.Generator().manual_seed(12))
        grad=torch.autograd.grad(sample.discrete_log_prob.sum(),self.logits)[0]
        expected=torch.bincount(sample.actions,minlength=2).double()[None,:]-20*self.logits.softmax(-1)
        torch.testing.assert_close(grad,expected)
        self.assertIsNone(torch.autograd.grad(sample.discrete_log_prob.sum(),self.head.mean_layer.bias,allow_unused=True)[0])

    def test_enumerated_conditional_kl_keeps_structural_gradient(self):
        theta=torch.tensor(.2,dtype=torch.float64,requires_grad=True)
        def objective(value):
            logits=torch.stack((.4+value,-.2-.3*value))[None,:]
            conditional=torch.stack((.1+value.square(),.8+.4*value))[None,:]
            return enumerated_continuous_kl(logits,conditional)
        actual=torch.autograd.grad(objective(theta),theta)[0]
        eps=1e-6;fd=(objective(theta.detach()+eps)-objective(theta.detach()-eps))/(2*eps)
        torch.testing.assert_close(actual,fd,rtol=1e-8,atol=1e-10)
        logits=torch.stack((.4+theta,-.2-.3*theta))[None,:]
        conditional=torch.stack((.1+theta.square(),.8+.4*theta))[None,:]
        incomplete=torch.autograd.grad((logits.softmax(-1).detach()*conditional).sum(),theta)[0]
        self.assertGreater(abs(float(actual-incomplete)),.05)

    def test_zero_reference_kl_and_strict_support(self):
        action=torch.tensor([1])
        self.assertAlmostEqual(self.head.normal_kl(self.hidden,action,self.head,self.hidden).item(),0.)
        self.assertAlmostEqual(categorical_kl(self.logits,self.logits).item(),0.)
        with self.assertRaises(ValueError):self.head.log_density(self.hidden,action,self.head.lower[None,:])

if __name__=='__main__':unittest.main()
