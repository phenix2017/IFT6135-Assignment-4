# stdlib imports
import argparse
import os

# thirdparty imports
from torch.utils.data import DataLoader
from torchvision import datasets, transforms
import torch
from torch.autograd import Variable
import torch.nn as nn
import torch.optim as optim
import numpy as np

# from project
from helpers import load_model, save_checkpoint, save_image_sample, save_learning_curve, save_learning_curve_epoch


def main(train_set, learning_rate, n_epochs, beta_0, beta_1,
         batch_size, num_workers, hidden_size, model_file,
         cuda, display_result_every, checkpoint_interval,
         seed, label_smoothing, grad_clip, dropout, upsampling):

    #  make data between -1 and 1
    data_transform = transforms.Compose([transforms.ToTensor(),
                                         transforms.Normalize(mean=(0.5, 0.5, 0.5), std=(0.5, 0.5, 0.5))])

    train_dataset = datasets.ImageFolder(root=os.path.join(os.getcwd(), train_set),
                                         transform=data_transform)

    train_dataloader = DataLoader(train_dataset, batch_size=batch_size,
                                  shuffle=True, num_workers=num_workers,
                                  drop_last=True)

    # initialize model
    if model_file:
        try:
            total_examples, fixed_noise, gen_losses, disc_losses, gen_loss_per_epoch, \
            disc_loss_per_epoch, prev_epoch, gen, disc = load_model(model_file, hidden_size, upsampling, cuda)
            print('model loaded successfully!')
        except:
            print('could not load model! creating new model...')
            model_file = None

    if not model_file:
        print('creating new model...')
        if upsampling == 'transpose':
            from models.model import Generator, Discriminator
        elif upsampling == 'nn':
            from models.model_nn import Generator, Discriminator
        elif upsampling == 'bilinear':
            from models.model_bilinear import Generator, Discriminator

        gen = Generator(hidden_dim=hidden_size, leaky=0.2, dropout=dropout)
        disc = Discriminator(leaky=0.2, dropout=dropout)

        gen.weight_init(mean=0, std=0.02)
        disc.weight_init(mean=0, std=0.02)

        total_examples = 0
        disc_losses = []
        gen_losses = []
        disc_loss_per_epoch = []
        gen_loss_per_epoch = []
        prev_epoch = 0

        #  Sample minibatch of m noise samples from noise prior p_g(z) and transform
        if cuda:
            fixed_noise = Variable(torch.randn(9, hidden_size).cuda())
        else:
            fixed_noise = Variable(torch.rand(9, hidden_size))

    if cuda:
        gen.cuda()
        disc.cuda()

    # Binary Cross Entropy loss
    BCE_loss = nn.BCELoss()

    # Adam optimizer
    gen_optimizer = optim.Adam(gen.parameters(), lr=learning_rate, betas=(beta_0, beta_1), eps=1e-8)
    disc_optimizer = optim.Adam(disc.parameters(), lr=learning_rate, betas=(beta_0, beta_1), eps=1e-8)

    # results save folder
    gen_images_dir = 'results/generated_images'
    train_summaries_dir = 'results/training_summaries'
    checkpoint_dir = 'results/checkpoints'
    if not os.path.isdir('results'):
        os.mkdir('results')
    if not os.path.isdir(gen_images_dir):
        os.mkdir(gen_images_dir)
    if not os.path.isdir(train_summaries_dir):
        os.mkdir(train_summaries_dir)
    if not os.path.isdir(checkpoint_dir):
        os.mkdir(checkpoint_dir)

    np.random.seed(seed)  # reset training seed to ensure that batches remain the same between runs!

    try:
        for epoch in range(prev_epoch, n_epochs):
            disc_losses_epoch = []
            gen_losses_epoch = []
            for idx, (true_batch, _) in enumerate(train_dataloader):
                disc.zero_grad()

                #  hack 6 of https://github.com/soumith/ganhacks
                if label_smoothing:
                    true_target = torch.FloatTensor(batch_size).uniform_(0.7, 1.2)
                else:
                    true_target = torch.ones(batch_size)

                #  Sample  minibatch  of examples from data generating distribution
                if cuda:
                    true_batch = Variable(true_batch.cuda())
                    true_target = Variable(true_target.cuda())
                else:
                    true_batch = Variable(true_batch)
                    true_target = Variable(true_target)

                #  train discriminator on true data
                true_disc_result = disc.forward(true_batch)
                disc_train_loss_true = BCE_loss(true_disc_result.squeeze(), true_target)
                disc_train_loss_true.backward()
                torch.nn.utils.clip_grad_norm(disc.parameters(), grad_clip)

                #  Sample minibatch of m noise samples from noise prior p_g(z) and transform
                if label_smoothing:
                    fake_target = torch.FloatTensor(batch_size).uniform_(0, 0.3)
                else:
                    fake_target = torch.zeros(batch_size)

                if cuda:
                    z = Variable(torch.randn(batch_size, hidden_size).cuda())
                    fake_target = Variable(fake_target.cuda())
                else:
                    z = Variable(torch.randn(batch_size, hidden_size))
                    fake_target = Variable(fake_target)

                #  train discriminator on fake data
                fake_batch = gen.forward(z.view(-1, hidden_size, 1, 1))
                fake_disc_result = disc.forward(fake_batch.detach())  # detach so gradients not computed for generator
                disc_train_loss_false = BCE_loss(fake_disc_result.squeeze(), fake_target)
                disc_train_loss_false.backward()
                torch.nn.utils.clip_grad_norm(disc.parameters(), grad_clip)
                disc_optimizer.step()

                #  compute performance statistics
                disc_train_loss = disc_train_loss_true + disc_train_loss_false
                disc_losses_epoch.append(disc_train_loss.data[0])

                disc_fake_accuracy = 1 - fake_disc_result.mean().data[0]
                disc_true_accuracy = true_disc_result.mean().data[0]

                #  Sample minibatch of m noise samples from noise prior p_g(z) and transform
                if label_smoothing:
                    true_target = torch.FloatTensor(batch_size).uniform_(0.7, 1.2)
                else:
                    true_target = torch.ones(batch_size)

                if cuda:
                    z = Variable(torch.randn(batch_size, hidden_size).cuda())
                    true_target = Variable(true_target.cuda())
                else:
                    z = Variable(torch.rand(batch_size, hidden_size))
                    true_target = Variable(true_target)

                # train generator
                gen.zero_grad()
                fake_batch = gen.forward(z.view(-1, hidden_size, 1, 1))
                disc_result = disc.forward(fake_batch)
                gen_train_loss = BCE_loss(disc_result.squeeze(), true_target)

                gen_train_loss.backward()
                torch.nn.utils.clip_grad_norm(gen.parameters(), grad_clip)
                gen_optimizer.step()
                gen_losses_epoch.append(gen_train_loss.data[0])

                if (total_examples != 0) and (total_examples % display_result_every == 0):
                    print('epoch {}: step {}/{} disc true acc: {:.4f} disc fake acc: {:.4f} '
                          'disc loss: {:.4f}, gen loss: {:.4f}'
                          .format(epoch+1, idx+1, len(train_dataloader), disc_true_accuracy, disc_fake_accuracy,
                                  disc_train_loss.data[0], gen_train_loss.data[0]))

                # Checkpoint model
                total_examples += batch_size
                if (total_examples != 0) and (total_examples % checkpoint_interval == 0):

                    disc_losses.extend(disc_losses_epoch)
                    gen_losses.extend(gen_losses_epoch)
                    save_checkpoint(total_examples=total_examples, fixed_noise=fixed_noise, disc=disc, gen=gen,
                                    gen_losses=gen_losses, disc_losses=disc_losses,
                                    disc_loss_per_epoch=disc_loss_per_epoch,
                                    gen_loss_per_epoch=gen_loss_per_epoch, epoch=epoch, directory=checkpoint_dir)
                    print("Checkpoint saved!")

                    #  sample images for inspection
                    save_image_sample(batch=gen.forward(fixed_noise.view(-1, hidden_size, 1, 1)),
                                      cuda=cuda, total_examples=total_examples, directory=gen_images_dir)
                    print("Saved images!")

                    # save learning curves for inspection
                    save_learning_curve(gen_losses=gen_losses, disc_losses=disc_losses, total_examples=total_examples,
                                        directory=train_summaries_dir)
                    print("Saved learning curves!")

            disc_loss_per_epoch.append(np.average(disc_losses_epoch))
            gen_loss_per_epoch.append(np.average(gen_losses_epoch))

            # Save epoch learning curve
            save_learning_curve_epoch(gen_losses=gen_loss_per_epoch, disc_losses=disc_loss_per_epoch,
                                      total_epochs=epoch+1, directory=train_summaries_dir)
            print("Saved learning curves!")

            print('epoch {}/{} disc loss: {:.4f}, gen loss: {:.4f}'
                  .format(epoch+1, n_epochs, np.array(disc_losses_epoch).mean(), np.array(gen_losses_epoch).mean()))

            disc_losses.extend(disc_losses_epoch)
            gen_losses.extend(gen_losses_epoch)

    except KeyboardInterrupt:
        print("Saving before quit...")
        save_checkpoint(total_examples=total_examples, fixed_noise=fixed_noise, disc=disc, gen=gen,
                        disc_loss_per_epoch=disc_loss_per_epoch,
                        gen_loss_per_epoch=gen_loss_per_epoch,
                        gen_losses=gen_losses, disc_losses=disc_losses, epoch=epoch, directory=checkpoint_dir)
        print("Checkpoint saved!")

        # sample images for inspection
        save_image_sample(batch=gen.forward(fixed_noise.view(-1, hidden_size, 1, 1)),
                          cuda=cuda, total_examples=total_examples, directory=gen_images_dir)
        print("Saved images!")

        # save learning curves for inspection
        save_learning_curve(gen_losses=gen_losses, disc_losses=disc_losses, total_examples=total_examples,
                            directory=train_summaries_dir)
        print("Saved learning curves!")


if __name__ == '__main__':

    # Parse command line arguments
    argparser = argparse.ArgumentParser()
    argparser.add_argument('--train_set', type=str, default='data/resized_celebA')
    argparser.add_argument('--learning_rate', type=float, default=0.0002)
    argparser.add_argument('--n_epochs', type=int, default=30)
    argparser.add_argument('--batch_size', type=int, default=128)
    argparser.add_argument('--beta_0', type=float, default=0.5)
    argparser.add_argument('--beta_1', type=float, default=0.999)
    argparser.add_argument('--num_workers', type=int, default=8)
    argparser.add_argument('--hidden_size', type=int, default=100)
    argparser.add_argument('--model_file', type=str, default=None)
    argparser.add_argument('--cuda', action='store_true', default=False)
    argparser.add_argument('--display_result_every', type=int, default=640)   # 640
    argparser.add_argument('--checkpoint_interval', type=int, default=32000)  # 32000
    argparser.add_argument('--seed', type=int, default=1024)
    argparser.add_argument('--label_smoothing', action='store_true', default=True)
    argparser.add_argument('--grad_clip', type=int, default=10)
    argparser.add_argument('--dropout', type=float, default=0.4)
    argparser.add_argument('--upsampling', type=str, default='transpose',
                           help="'transpose', 'nn' or 'bilinear'")
    args = argparser.parse_args()

    args.cuda = args.cuda and torch.cuda.is_available()

    main(train_set=args.train_set,
         learning_rate=args.learning_rate,
         n_epochs=args.n_epochs,
         beta_0=args.beta_0,
         beta_1=args.beta_1,
         batch_size=args.batch_size,
         num_workers=args.num_workers,
         hidden_size=args.hidden_size,
         model_file=args.model_file,
         cuda=args.cuda,
         display_result_every=args.display_result_every,
         checkpoint_interval=args.checkpoint_interval,
         seed=args.seed,
         label_smoothing=args.label_smoothing,
         grad_clip=args.grad_clip,
         dropout=args.dropout,
         upsampling=args.upsampling)
